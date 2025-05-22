import json
import logging
from datetime import datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import Q
from .models import Message
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
import cloudinary.uploader
from django.conf import settings

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_metadata = None

    async def connect(self):
        self.room_group_name = None
        self.sender_id = self.scope["url_route"]["kwargs"]["sender_id"]
        self.receiver_id = self.scope["url_route"]["kwargs"]["receiver_id"]

        try:
            self.sender_id = int(self.sender_id)
            self.receiver_id = int(self.receiver_id)
        except ValueError:
            logger.error("Invalid sender or receiver ID")
            await self.close(code=1000)
            return

        query_string = self.scope['query_string'].decode()
        token = dict(q.split('=') for q in query_string.split('&') if '=' in q).get('token', None)
        if not token:
            logger.error("No token provided")
            await self.close(code=1008)
            return

        try:
            user = await self.authenticate_token(token)
            if str(user.id) != str(self.sender_id):
                logger.error("User ID does not match sender ID")
                await self.close(code=1008)
                return
            self.user = user
        except AuthenticationFailed as e:
            logger.error(f"Authentication failed: {str(e)}")
            await self.close(code=1008)
            return

        self.room_group_name = f"chat_{min(self.sender_id, self.receiver_id)}_{max(self.sender_id, self.receiver_id)}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        logger.debug(f"WebSocket connected for {self.sender_id} to {self.receiver_id}")

    async def disconnect(self, close_code):
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        logger.debug(f"WebSocket disconnected: {close_code}")

    async def receive(self, text_data=None, bytes_data=None):
            if text_data:
                try:
                    data = json.loads(text_data)
                    logger.debug(f"Received message: {data}")

                    # Handle ping
                    if data.get("type") == "ping":
                        await self.send(text_data=json.dumps({"type": "pong"}))
                        logger.debug("Sent pong response")
                        return

                    # Handle history request
                    if data.get("request_history"):
                        page = data.get("page", 1)
                        page_size = data.get("page_size", 50)
                        messages = await self.get_chat_history(self.sender_id, self.receiver_id, page, page_size)
                        await self.send(text_data=json.dumps({"messages": messages}))
                        logger.debug(f"Sent {len(messages)} history messages for page {page}")
                        return

                    # Handle regular message
                    if "message" in data:
                        encrypted_message = data.get("message")
                        nonce = data.get("nonce")
                        ephemeral_key = data.get("ephemeral_key")
                        message_key = data.get("message_key")
                        message_type = data.get("type", "text")
                        message_id = data.get("message_id")
                        if not encrypted_message:
                            await self.send(text_data=json.dumps({"error": "Message cannot be empty"}))
                            return
                        if not message_id:
                            await self.send(text_data=json.dumps({"error": "message_id is required"}))
                            return

                        receiver = await self.get_user_by_id(self.receiver_id)
                        if not receiver:
                            await self.send(text_data=json.dumps({"error": "User does not exist"}))
                            return

                        await self.save_encrypted_message(
                            self.sender_id,
                            self.receiver_id,
                            encrypted_message,
                            nonce,
                            ephemeral_key,
                            message_key,
                            message_id,
                            message_type
                        )

                        await self.channel_layer.group_send(
                            self.room_group_name,
                            {
                                "type": "chat_message",
                                "message": encrypted_message,
                                "nonce": nonce,
                                "ephemeral_key": ephemeral_key,
                                "message_key": message_key,
                                "sender": self.sender_id,
                                "receiver": self.receiver_id,
                                "message_type": message_type,
                                "timestamp": data.get("timestamp", datetime.now().isoformat()),
                                "message_id": message_id
                            }
                        )
                    else:
                        self.pending_metadata = data
                except json.JSONDecodeError:
                    logger.error("Invalid JSON received")
                    await self.send(text_data=json.dumps({"error": "Invalid message format"}))
                except Exception as e:
                    logger.error(f"Error processing message: {str(e)}")
                    await self.send(text_data=json.dumps({"error": str(e)}))

            if bytes_data:
                try:
                    metadata = self.pending_metadata or {}
                    file_name = metadata.get("file_name", f"unnamed_file_{datetime.now().timestamp()}")
                    file_type = metadata.get("file_type", "application/octet-stream")
                    file_size = metadata.get("file_size") or len(bytes_data)
                    nonce = metadata.get("nonce")
                    ephemeral_key = metadata.get("ephemeral_key")
                    message_key = metadata.get("message_key")
                    message_type = metadata.get("type", "file")
                    timestamp = metadata.get("timestamp", datetime.now().isoformat())
                    message_id = metadata.get("message_id")
                    if not message_id:
                        await self.send(text_data=json.dumps({"error": "message_id is required"}))
                        return

                    receiver = await self.get_user_by_id(self.receiver_id)
                    if not receiver:
                        await self.send(text_data=json.dumps({"error": "User does not exist"}))
                        return

                    # Upload file to Cloudinary
                    upload_result = await database_sync_to_async(cloudinary.uploader.upload)(
                        bytes_data,
                        folder=f'chat_files/{self.sender_id}',
                        resource_type='auto'
                    )

                    message = await self.save_file_message(
                        self.sender_id,
                        self.receiver_id,
                        upload_result['secure_url'],
                        file_name,
                        upload_result['resource_type'] + '/' + upload_result['format'],
                        upload_result['bytes'],
                        nonce,
                        ephemeral_key,
                        message_key,
                        message_id,
                        message_type
                    )

                    file_url = upload_result['secure_url']

                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "sender": self.sender_id,
                            "receiver": self.receiver_id,
                            "message_type": message_type,
                            "file_name": file_name,
                            "file_type": upload_result['resource_type'] + '/' + upload_result['format'],
                            "file_url": file_url,
                            "file_size": upload_result['bytes'],
                            "nonce": nonce,
                            "ephemeral_key": ephemeral_key,
                            "message_key": message_key,
                            "timestamp": timestamp,
                            "message_id": message_id
                        }
                    )
                    self.pending_metadata = None
                except Exception as e:
                    logger.error(f"Error processing file: {str(e)}")
                    await self.send(text_data=json.dumps({"error": str(e)}))

        async def chat_message(self, event):
            message_data = {
                "sender": event["sender"],
                "receiver": event["receiver"],
                "message": event.get("message"),
                "nonce": event.get("nonce"),
                "ephemeral_key": event.get("ephemeral_key"),
                "message_key": event.get("message_key"),
                "type": event.get("message_type", "text"),
                "file_name": event.get("file_name"),
                "file_type": event.get("file_type"),
                "file_url": event.get("file_url"),
                "file_size": event.get("file_size"),
                "timestamp": event.get("timestamp", datetime.now().isoformat()),
                "message_id": event.get("message_id")
            }
            logger.debug(f"Sending chat message: {message_data}")
            await self.send(text_data=json.dumps(message_data))

        @database_sync_to_async
        def save_encrypted_message(self, sender_id, receiver_id, encrypted_message, nonce, ephemeral_key, message_key, message_id, message_type='text'):
            sender = User.objects.get(id=sender_id)
            receiver = User.objects.get(id=receiver_id)
            if Message.objects.filter(message_id=message_id).exists():
                raise ValueError("message_id must be unique")
            return Message.objects.create(
                message_id=message_id,
                sender=sender,
                receiver=receiver,
                content=encrypted_message,
                nonce=nonce or '',
                ephemeral_key=ephemeral_key or '',
                message_key=message_key or '',
                type=message_type
            )

        @database_sync_to_async
        def save_file_message(self, sender_id, receiver_id, file_url, file_name, file_type, file_size, nonce, ephemeral_key, message_key, message_id, message_type='file'):
            sender = User.objects.get(id=sender_id)
            receiver = User.objects.get(id=receiver_id)
            if Message.objects.filter(message_id=message_id).exists():
                raise ValueError("message_id must be unique")
            message = Message.objects.create(
                message_id=message_id,
                sender=sender,
                receiver=receiver,
                content="",
                file=file_url,  # Store Cloudinary URL directly
                file_name=file_name,
                file_type=file_type,
                file_size=file_size,
                nonce=nonce or '',
                ephemeral_key=ephemeral_key or '',
                message_key=message_key or '',
                type=message_type
            )
            return message

        @database_sync_to_async
        def get_user_by_id(self, user_id):
            try:
                return User.objects.get(id=user_id)
            except User.DoesNotExist:
                return None

        @database_sync_to_async
        def get_chat_history(self, sender_id, receiver_id, page=1, page_size=50):
            try:
                offset = (page - 1) * page_size
                messages = Message.objects.filter(
                    (Q(sender_id=sender_id) & Q(receiver_id=receiver_id)) |
                    (Q(sender_id=receiver_id) & Q(receiver_id=sender_id))
                ).order_by('created_at')[offset:offset + page_size].only(
                    'message_id', 'sender_id', 'receiver_id', 'content', 'nonce',
                    'ephemeral_key', 'message_key', 'created_at', 'file_name',
                    'file_type', 'file', 'file_size', 'type'
                )
                return [
                    {
                        "sender": msg.sender.id,
                        "receiver": msg.receiver.id,
                        "message": msg.content,
                        "nonce": msg.nonce,
                        "ephemeral_key": msg.ephemeral_key,
                        "message_key": msg.message_key,
                        "created_at": msg.created_at.isoformat(),
                        "type": msg.type,
                        "file_name": msg.file_name,
                        "file_type": msg.file_type,
                        "file_url": msg.file.url if msg.file else None,  # Use Cloudinary URL
                        "file_size": msg.file_size,
                        "message_id": msg.message_id
                    }
                    for msg in messages
                ]
            except Exception as e:
                logger.error(f"Error fetching chat history: {str(e)}")
                return []

        @database_sync_to_async
        def authenticate_token(self, token):
            jwt_auth = JWTAuthentication()
            validated_token = jwt_auth.get_validated_token(token)
            return jwt_auth.get_user(validated_token)

class GlobalConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_name = "global_group"
        query_string = self.scope['query_string'].decode()
        token = dict(q.split('=') for q in query_string.split('&') if '=' in q).get('token', None)

        if not token:
            await self.close(code=1008)
            return

        try:
            user = await self.authenticate_token(token)
            self.user = user
        except AuthenticationFailed:
            await self.close(code=1008)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                if data.get("type") == "update_last_seen":
                    await self.update_last_seen()
                    await self.channel_layer.group_send(
                        self.group_name,
                        {
                            "type": "last_seen_update",
                            "username": self.user.username,
                            "last_seen": self.user.last_seen.isoformat() if self.user.last_seen else None,
                        }
                    )
            except json.JSONDecodeError:
                await self.send(text_data=json.dumps({"error": "Invalid JSON data"}))

    async def last_seen_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "last_seen_update",
            "username": event["username"],
            "last_seen": event["last_seen"],
        }))

    @database_sync_to_async
    def authenticate_token(self, token):
        jwt_auth = JWTAuthentication()
        validated_token = jwt_auth.get_validated_token(token)
        return jwt_auth.get_user(validated_token)

    @database_sync_to_async
    def update_last_seen(self):
        self.user.last_seen = datetime.now()
        self.user.save()
        logger.info(f"Updated last_seen for user {self.user.username} to {self.user.last_seen}")