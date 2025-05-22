import json
import logging
from datetime import datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import Q
from django.core.files.base import ContentFile
from django.conf import settings
from .models import Message
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed

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

                # Handle history request with pagination
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
                # Handle file metadata (Cloudinary URL)
                elif "file_url" in data:
                    file_name = data.get("file_name", f"unnamed_file_{datetime.now().timestamp()}")
                    file_type = data.get("file_type", "application/octet-stream")
                    file_size = data.get("file_size")
                    nonce = data.get("nonce")
                    ephemeral_key = data.get("ephemeral_key")
                    message_key = data.get("message_key")
                    message_type = data.get("type", "file")
                    timestamp = data.get("timestamp", datetime.now().isoformat())
                    message_id = data.get("message_id")
                    file_url = data.get("file_url")
                    public_id = data.get("public_id")

                    if not message_id:
                        await self.send(text_data=json.dumps({"error": "message_id is required"}))
                        return

                    receiver = await self.get_user_by_id(self.receiver_id)
                    if not receiver:
                        await self.send(text_data=json.dumps({"error": "User does not exist"}))
                        return

                    # Validate file type and size
                    allowed_types = ['image/jpeg', 'image/png', 'video/mp4', 'audio/mpeg', 'application/pdf']
                    if file_type not in allowed_types:
                        raise ValueError("Unsupported file type")
                    if file_size > 100 * 1024 * 1024:
                        raise ValueError("File size exceeds 100MB limit")

                    message = await self.save_cloudinary_file_message(
                        self.sender_id,
                        self.receiver_id,
                        file_url,
                        file_name,
                        file_type,
                        file_size,
                        nonce,
                        ephemeral_key,
                        message_key,
                        message_id,
                        message_type,
                        public_id
                    )

                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "chat_message",
                            "sender": self.sender_id,
                            "receiver": self.receiver_id,
                            "message_type": message_type,
                            "file_name": file_name,
                            "file_type": file_type,
                            "file_url": file_url,
                            "file_size": file_size,
                            "nonce": nonce,
                            "ephemeral_key": ephemeral_key,
                            "message_key": message_key,
                            "timestamp": timestamp,
                            "message_id": message_id,
                            "public_id": public_id
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

    async def chat_message(self, event):
        message_data = {
            "sender": event["sender"],
            "receiver": event["receiver"],
            "message_type": event.get("message_type", "text"),
            "timestamp": event["timestamp"],
            "message_id": event["message_id"],
        }
        if event["message_type"] == "text":
            message_data.update({
                "message": event["message"],
                "nonce": event["nonce"],
                "ephemeral_key": event["ephemeral_key"],
                "message_key": event["message_key"],
            })
        else:
            message_data.update({
                "file_name": event.get("file_name"),
                "file_type": event.get("file_type"),
                "file_url": event.get("file_url"),
                "file_size": event.get("file_size"),
                "nonce": event.get("nonce"),
                "ephemeral_key": event.get("ephemeral_key"),
                "message_key": event.get("message_key"),
                "public_id": event.get("public_id"),
            })
        await self.send(text_data=json.dumps(message_data))

    @database_sync_to_async
    def authenticate_token(self, token):
        jwt_auth = JWTAuthentication()
        validated_token = jwt_auth.get_validated_token(token)
        return jwt_auth.get_user(validated_token)

    @database_sync_to_async
    def get_user_by_id(self, user_id):
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

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
    def save_file_message(self, sender_id, receiver_id, file_data, file_name, file_type, file_size, nonce, ephemeral_key, message_key, message_id, message_type='file'):
        sender = User.objects.get(id=sender_id)
        receiver = User.objects.get(id=receiver_id)
        if file_size > 100 * 1024 * 1024:
            raise ValueError("File size exceeds 100MB limit")
        allowed_types = ['image/jpeg', 'image/png', 'video/mp4', 'audio/mpeg', 'application/pdf']
        if file_type not in allowed_types:
            raise ValueError("Unsupported file type")
        if Message.objects.filter(message_id=message_id).exists():
            raise ValueError("message_id must be unique")
        message = Message.objects.create(
            message_id=message_id,
            sender=sender,
            receiver=receiver,
            content="",
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            nonce=nonce or '',
            ephemeral_key=ephemeral_key or '',
            message_key=message_key or '',
            type=message_type
        )
        message.file.save(file_name, ContentFile(file_data))
        return message

    @database_sync_to_async
    def save_cloudinary_file_message(self, sender_id, receiver_id, file_url, file_name, file_type, file_size, nonce, ephemeral_key, message_key, message_id, message_type='file', public_id=None):
        sender = User.objects.get(id=sender_id)
        receiver = User.objects.get(id=receiver_id)
        if Message.objects.filter(message_id=message_id).exists():
            raise ValueError("message_id must be unique")
        message = Message.objects.create(
            message_id=message_id,
            sender=sender,
            receiver=receiver,
            content="",
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            nonce=nonce or '',
            ephemeral_key=ephemeral_key or '',
            message_key=message_key or '',
            type=message_type,
            file=file_url,  # Store Cloudinary URL directly
            public_id=public_id
        )
        return message

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
                'file_type', 'file', 'file_size', 'type', 'public_id'
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
                    "file_url": msg.file.url if msg.file else None,
                    "file_size": msg.file_size,
                    "message_id": msg.message_id,
                    "public_id": msg.public_id
                }
                for msg in messages
            ]
        except Exception as e:
            logger.error(f"Error fetching chat history: {str(e)}")
            return []

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

class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.group_id = self.scope["url_route"]["kwargs"]["group_id"]
        self.room_group_name = f"group_{self.group_id}"

        query_string = self.scope['query_string'].decode()
        token = dict(q.split('=') for q in query_string.split('&') if '=' in q).get('token', None)
        if not token:
            logger.error("No token provided")
            await self.close(code=1008)
            return

        try:
            user = await self.authenticate_token(token)
            self.user = user
        except AuthenticationFailed as e:
            logger.error(f"Authentication failed: {str(e)}")
            await self.close(code=1008)
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()
        logger.debug(f"GroupChatConsumer connected for user {self.user.id} to groups: ['{self.room_group_name}']")

    async def disconnect(self, close_code):
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        logger.debug(f"GroupChatConsumer disconnected: {close_code}")

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                logger.debug(f"Received group message: {data}")

                # Handle ping
                if data.get("type") == "ping":
                    await self.send(text_data=json.dumps({"type": "pong"}))
                    logger.debug("Sent pong response")
                    return

                # Handle group message
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

                    await self.save_group_message(
                        self.user.id,
                        self.group_id,
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
                            "type": "group_message",
                            "message": encrypted_message,
                            "nonce": nonce,
                            "ephemeral_key": ephemeral_key,
                            "message_key": message_key,
                            "sender": self.user.id,
                            "group_id": self.group_id,
                            "message_type": message_type,
                            "timestamp": data.get("timestamp", datetime.now().isoformat()),
                            "message_id": message_id
                        }
                    )
                # Handle file metadata (Cloudinary URL)
                elif "file_url" in data:
                    file_name = data.get("file_name", f"unnamed_file_{datetime.now().timestamp()}")
                    file_type = data.get("file_type", "application/octet-stream")
                    file_size = data.get("file_size")
                    nonce = data.get("nonce")
                    ephemeral_key = data.get("ephemeral_key")
                    message_key = data.get("message_key")
                    message_type = data.get("type", "file")
                    timestamp = data.get("timestamp", datetime.now().isoformat())
                    message_id = data.get("message_id")
                    file_url = data.get("file_url")
                    public_id = data.get("public_id")

                    if not message_id:
                        await self.send(text_data=json.dumps({"error": "message_id is required"}))
                        return

                    # Validate file type and size
                    allowed_types = ['image/jpeg', 'image/png', 'video/mp4', 'audio/mpeg', 'application/pdf']
                    if file_type not in allowed_types:
                        raise ValueError("Unsupported file type")
                    if file_size > 100 * 1024 * 1024:
                        raise ValueError("File size exceeds 100MB limit")

                    message = await self.save_group_cloudinary_file_message(
                        self.user.id,
                        self.group_id,
                        file_url,
                        file_name,
                        file_type,
                        file_size,
                        nonce,
                        ephemeral_key,
                        message_key,
                        message_id,
                        message_type,
                        public_id
                    )

                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "group_message",
                            "sender": self.user.id,
                            "group_id": self.group_id,
                            "message_type": message_type,
                            "file_name": file_name,
                            "file_type": file_type,
                            "file_url": file_url,
                            "file_size": file_size,
                            "nonce": nonce,
                            "ephemeral_key": ephemeral_key,
                            "message_key": message_key,
                            "timestamp": timestamp,
                            "message_id": message_id,
                            "public_id": public_id
                        }
                    )
            except json.JSONDecodeError:
                logger.error("Invalid JSON received")
                await self.send(text_data=json.dumps({"error": "Invalid message format"}))
            except Exception as e:
                logger.error(f"Error processing group message: {str(e)}")
                await self.send(text_data=json.dumps({"error": str(e)}))

    async def group_message(self, event):
        message_data = {
            "sender": event["sender"],
            "group_id": event["group_id"],
            "message_type": event.get("message_type", "text"),
            "timestamp": event["timestamp"],
            "message_id": event["message_id"],
        }
        if event["message_type"] == "text":
            message_data.update({
                "message": event["message"],
                "nonce": event["nonce"],
                "ephemeral_key": event["ephemeral_key"],
                "message_key": event["message_key"],
            })
        else:
            message_data.update({
                "file_name": event.get("file_name"),
                "file_type": event.get("file_type"),
                "file_url": event.get("file_url"),
                "file_size": event.get("file_size"),
                "nonce": event.get("nonce"),
                "ephemeral_key": event.get("ephemeral_key"),
                "message_key": event["message_key"],
                "public_id": event.get("public_id"),
            })
        await self.send(text_data=json.dumps(message_data))

    @database_sync_to_async
    def authenticate_token(self, token):
        jwt_auth = JWTAuthentication()
        validated_token = jwt_auth.get_validated_token(token)
        return jwt_auth.get_user(validated_token)

    @database_sync_to_async
    def save_group_message(self, sender_id, group_id, encrypted_message, nonce, ephemeral_key, message_key, message_id, message_type='text'):
        # Note: Assumes a GroupMessage model or similar; adjust based on your actual model
        sender = User.objects.get(id=sender_id)
        if Message.objects.filter(message_id=message_id).exists():
            raise ValueError("message_id must be unique")
        return Message.objects.create(
            message_id=message_id,
            sender=sender,
            receiver=None,  # No receiver for group messages
            content=encrypted_message,
            nonce=nonce or '',
            ephemeral_key=ephemeral_key or '',
            message_key=message_key or '',
            type=message_type
        )

    @database_sync_to_async
    def save_group_cloudinary_file_message(self, sender_id, group_id, file_url, file_name, file_type, file_size, nonce, ephemeral_key, message_key, message_id, message_type='file', public_id=None):
        sender = User.objects.get(id=sender_id)
        if Message.objects.filter(message_id=message_id).exists():
            raise ValueError("message_id must be unique")
        message = Message.objects.create(
            message_id=message_id,
            sender=sender,
            receiver=None,  # No receiver for group messages
            content="",
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            nonce=nonce or '',
            ephemeral_key=ephemeral_key or '',
            message_key=message_key or '',
            type=message_type,
            file=file_url,  # Store Cloudinary URL directly
            public_id=public_id
        )
        return message