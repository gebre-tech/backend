import json
import os
from datetime import datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import Q
from .models import Message
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.core.files.base import ContentFile
from django.conf import settings
import logging

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
            await self.close(code=1000)
            return

        query_string = self.scope['query_string'].decode()
        token = dict(q.split('=') for q in query_string.split('&') if '=' in q).get('token', None)
        if not token:
            await self.close(code=1008)
            return

        try:
            user = await self.authenticate_token(token)
            if str(user.id) != str(self.sender_id):
                await self.close(code=1008)
                return
            self.user = user
        except AuthenticationFailed:
            await self.close(code=1008)
            return

        self.room_group_name = f"chat_{min(self.sender_id, self.receiver_id)}_{max(self.sender_id, self.receiver_id)}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        messages = await self.get_chat_history(self.sender_id, self.receiver_id)
        await self.send(text_data=json.dumps({"messages": messages}))

    async def disconnect(self, close_code):
        if self.room_group_name:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                if data.get("request_history"):
                    messages = await self.get_chat_history(self.sender_id, self.receiver_id)
                    await self.send(text_data=json.dumps({"messages": messages}))
                elif "message" in data:
                    encrypted_message = data.get("message")
                    nonce = data.get("nonce")
                    ephemeral_key = data.get("ephemeral_key")
                    message_key = data.get("message_key")
                    message_type = data.get("type", "text")
                    if not encrypted_message:
                        await self.send(text_data=json.dumps({"error": "Message cannot be empty"}))
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
                        message_key
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
                            "timestamp": data.get("timestamp", datetime.now().isoformat())
                        }
                    )
                else:
                    self.pending_metadata = data
            except json.JSONDecodeError:
                await self.send(text_data=json.dumps({"error": "Invalid JSON data"}))
            except Exception as e:
                await self.send(text_data=json.dumps({"error": str(e)}))

        if bytes_data:
            try:
                metadata = self.pending_metadata or {}
                file_name = metadata.get("file_name", f"unnamed_file_{datetime.now().timestamp()}")
                file_type = metadata.get("file_type", "application/octet-stream")
                nonce = metadata.get("nonce")
                ephemeral_key = metadata.get("ephemeral_key")
                message_key = metadata.get("message_key")
                message_type = metadata.get("type", "file")
                timestamp = metadata.get("timestamp", datetime.now().isoformat())

                receiver = await self.get_user_by_id(self.receiver_id)
                if not receiver:
                    await self.send(text_data=json.dumps({"error": "User does not exist"}))
                    return

                message = await self.save_file_message(
                    self.sender_id,
                    self.receiver_id,
                    bytes_data,
                    file_name,
                    file_type,
                    nonce,
                    ephemeral_key,
                    message_key
                )

                file_url = f"{settings.MEDIA_URL}{message.file.name}"
                full_file_url = f"{settings.SITE_URL}{file_url}"

                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message",
                        "sender": self.sender_id,
                        "receiver": self.receiver_id,
                        "message_type": message_type,
                        "file_name": file_name,
                        "file_type": file_type,
                        "file_url": full_file_url,
                        "nonce": nonce,
                        "ephemeral_key": ephemeral_key,
                        "message_key": message_key,
                        "timestamp": timestamp
                    }
                )
                self.pending_metadata = None
            except Exception as e:
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
            "timestamp": event.get("timestamp", datetime.now().isoformat())
        }
        await self.send(text_data=json.dumps(message_data))

    @database_sync_to_async
    def save_encrypted_message(self, sender_id, receiver_id, encrypted_message, nonce, ephemeral_key, message_key):
        sender = User.objects.get(id=sender_id)
        receiver = User.objects.get(id=receiver_id)
        return Message.objects.create(
            sender=sender,
            receiver=receiver,
            content=encrypted_message,
            nonce=nonce or '',
            ephemeral_key=ephemeral_key or '',
            message_key=message_key or ''
        )

    @database_sync_to_async
    def save_file_message(self, sender_id, receiver_id, file_data, file_name, file_type, nonce, ephemeral_key, message_key):
        sender = User.objects.get(id=sender_id)
        receiver = User.objects.get(id=receiver_id)
        message = Message.objects.create(
            sender=sender,
            receiver=receiver,
            content="",
            file_name=file_name,
            file_type=file_type,
            nonce=nonce or '',
            ephemeral_key=ephemeral_key or '',
            message_key=message_key or ''
        )
        message.file.save(file_name, ContentFile(file_data))
        return message

    @database_sync_to_async
    def get_user_by_id(self, user_id):
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def get_chat_history(self, sender_id, receiver_id):
        messages = Message.objects.filter(
            (Q(sender_id=sender_id) & Q(receiver_id=receiver_id)) |
            (Q(sender_id=receiver_id) & Q(receiver_id=sender_id))
        ).order_by('created_at').only('sender_id', 'receiver_id', 'content', 'nonce', 'ephemeral_key', 'message_key', 'created_at', 'file_name', 'file_type', 'file')
        return [
            {
                "sender": msg.sender.id,
                "receiver": msg.receiver.id,
                "message": msg.content,
                "nonce": msg.nonce,
                "ephemeral_key": msg.ephemeral_key,
                "message_key": msg.message_key,
                "created_at": msg.created_at.isoformat(),
                "type": "photo" if msg.file and msg.file_type.startswith('image/') else "video" if msg.file and msg.file_type.startswith('video/') else "file" if msg.file else "text",
                "file_name": msg.file_name,
                "file_type": msg.file_type,
                "file_url": msg.file.url if msg.file else None
            }
            for msg in messages
        ]

    @database_sync_to_async
    def authenticate_token(self, token):
        jwt_auth = JWTAuthentication()
        validated_token = jwt_auth.get_validated_token(token)
        return jwt_auth.get_user(validated_token)