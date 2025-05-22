import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.conf import settings
from channels.db import database_sync_to_async
from .models import Message
from .serializers import MessageSerializer
import base64
import os

logger = logging.getLogger(__name__)
User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.sender_id = self.scope['url_route']['kwargs']['sender_id']
        self.receiver_id = self.scope['url_route']['kwargs']['receiver_id']
        self.room_group_name = f'chat_{min(self.sender_id, self.receiver_id)}_{max(self.sender_id, self.receiver_id)}'

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                text_data_json = json.loads(text_data)
                message_id = text_data_json.get('message_id')
                content = text_data_json.get('content', '')
                nonce = text_data_json.get('nonce', '')
                ephemeral_key = text_data_json.get('ephemeral_key', '')
                message_key = text_data_json.get('message_key', '')
                message_type = text_data_json.get('type', 'text')

                if message_type == 'text':
                    message = await self.save_text_message(
                        self.sender_id, self.receiver_id, content, nonce,
                        ephemeral_key, message_key, message_id
                    )
                    serialized_message = MessageSerializer(message, context={'request': None}).data
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'chat_message',
                            'message': serialized_message
                        }
                    )
                elif message_type in ['photo', 'video', 'audio', 'file']:
                    # Store metadata for file message
                    self.file_metadata = {
                        'sender_id': self.sender_id,
                        'receiver_id': self.receiver_id,
                        'file_name': text_data_json.get('file_name'),
                        'file_type': text_data_json.get('file_type'),
                        'file_size': text_data_json.get('file_size'),
                        'nonce': nonce,
                        'ephemeral_key': ephemeral_key,
                        'message_key': message_key,
                        'message_id': message_id,
                        'message_type': message_type
                    }
            except json.JSONDecodeError:
                logger.error("Invalid JSON data received: %s", text_data)
                await self.send(text_data=json.dumps({'error': 'Invalid JSON data'}))
            except Exception as e:
                logger.error("Error processing text data: %s", str(e))
                await self.send(text_data=json.dumps({'error': str(e)}))

        elif bytes_data and hasattr(self, 'file_metadata'):
            try:
                message = await self.save_file_message(
                    self.file_metadata['sender_id'],
                    self.file_metadata['receiver_id'],
                    bytes_data,
                    self.file_metadata['file_name'],
                    self.file_metadata['file_type'],
                    self.file_metadata['file_size'],
                    self.file_metadata['nonce'],
                    self.file_metadata['ephemeral_key'],
                    self.file_metadata['message_key'],
                    self.file_metadata['message_id'],
                    self.file_metadata['message_type']
                )
                serialized_message = MessageSerializer(message, context={'request': None}).data
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'message': serialized_message
                    }
                )
                logger.info(f"File message saved with Cloudinary URL: {serialized_message['file_url']}")
                del self.file_metadata
            except Exception as e:
                logger.error("Error saving file message: %s", str(e))
                await self.send(text_data=json.dumps({'error': str(e)}))

    async def chat_message(self, event):
        message = event['message']
        await self.send(text_data=json.dumps(message))

    @database_sync_to_async
    def save_text_message(self, sender_id, receiver_id, content, nonce, ephemeral_key, message_key, message_id):
        sender = User.objects.get(id=sender_id)
        receiver = User.objects.get(id=receiver_id)
        if Message.objects.filter(message_id=message_id).exists():
            raise ValueError("message_id must be unique")
        return Message.objects.create(
            message_id=message_id,
            sender=sender,
            receiver=receiver,
            content=content,
            nonce=nonce,
            ephemeral_key=ephemeral_key,
            message_key=message_key,
            type='text'
        )

    @database_sync_to_async
    def save_file_message(self, sender_id, receiver_id, file_data, file_name, file_type, file_size, nonce, ephemeral_key, message_key, message_id, message_type='file'):
        sender = User.objects.get(id=sender_id)
        receiver = User.objects.get(id=receiver_id)
        if Message.objects.filter(message_id=message_id).exists():
            raise ValueError("message_id must be unique")

        # Create a message instance without saving the file yet
        message = Message(
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

        # Save the file to Cloudinary
        message.file.save(file_name, ContentFile(file_data))
        message.save()

        # Log the Cloudinary URL for debugging
        logger.debug(f"File uploaded to Cloudinary: {message.file.url}")

        return message

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
                    "file_url": msg.file.url if msg.file else None,
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