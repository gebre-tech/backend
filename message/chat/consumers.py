# chat/consumers.py
import json
import logging
import redis
from django.conf import settings
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatRoom, ChatMessage
from .serializers import ChatMessageSerializer, ChatRoomSerializer
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from datetime import datetime
from django.utils import timezone

User = get_user_model()
logger = logging.getLogger(__name__)

# Initialize Redis client
redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True
)

class BaseChatConsumer(AsyncWebsocketConsumer):
    async def connect(self, is_group=False):
        self.chat_id = self.scope["url_route"]["kwargs"]["chat_id"]
        self.chat_group_name = f"{'group_' if is_group else ''}chat_{self.chat_id}"
        token = (
            self.scope["query_string"].decode().split("token=")[1]
            if "token=" in self.scope["query_string"].decode()
            else None
        )

        if not token:
            await self.send_error("No token provided", 4001)
            return

        try:
            access_token = AccessToken(token)
            user_id = access_token["user_id"]
            self.user, self.chat_room = await self.validate_user_and_room(user_id)
            if self.chat_room.is_group != is_group:
                await self.send_error(
                    f"Use {'GroupChat' if is_group else 'Chat'}Consumer",
                    4005 + (1 if is_group else 0)
                )
                return
        except Exception as e:
            await self.send_error(
                str(e), 4003 if isinstance(e, AccessToken.TokenError) else 5000
            )
            return

        await self.accept()
        await self.channel_layer.group_add(self.chat_group_name, self.channel_name)
        logger.info(f"User {self.user.username} connected to {self.chat_group_name}")

    async def disconnect(self, close_code):
        if hasattr(self, "chat_group_name"):
            await self.channel_layer.group_discard(self.chat_group_name, self.channel_name)
        logger.info(f"Disconnected from {self.chat_group_name} with code {close_code}")

    async def send_error(self, message, code):
        await self.send(json.dumps({"error": message}))
        await self.close(code=code)

    @database_sync_to_async
    def validate_user_and_room(self, user_id):
        user = User.objects.get(id=user_id)
        chat_room = ChatRoom.objects.prefetch_related("members").get(id=self.chat_id)
        if not chat_room.members.filter(id=user_id).exists():
            raise ValueError("User not in chat")
        return user, chat_room

    @database_sync_to_async
    def create_message(self, sender, chat, content, message_type, attachment_url=None, forwarded_from=None, timestamp=None):
        # If timestamp is provided (from client), use it; otherwise, use current time
        timestamp = timestamp or timezone.now()
        try:
            message = ChatMessage.objects.create(
                sender=sender,
                chat=chat,
                content=content,
                message_type=message_type,
                attachment_url=attachment_url,
                forwarded_from=forwarded_from,
                timestamp=timestamp
            )
            message.delivered_to.add(*chat.members.all())
            return message
        except IntegrityError:
            # If a duplicate is detected, fetch the existing message
            existing_message = ChatMessage.objects.get(
                chat=chat,
                sender=sender,
                content=content,
                message_type=message_type,
                timestamp=timestamp
            )
            return existing_message

    @database_sync_to_async
    def serialize_message(self, message):
        return ChatMessageSerializer(message).data

    async def send_ack(self, client_id, server_id):
        await self.send(json.dumps({"type": "ack", "messageId": client_id, "serverId": server_id}))

    async def has_processed_message(self, message_id, user_id):
        key = f"chat:{self.chat_id}:user:{user_id}:processed_messages"
        return redis_client.sismember(key, message_id)

    async def mark_message_processed(self, message_id, user_id):
        key = f"chat:{self.chat_id}:user:{user_id}:processed_messages"
        redis_client.sadd(key, message_id)
        # Set an expiration time (e.g., 1 hour) to clean up Redis
        redis_client.expire(key, 3600)

class ChatConsumer(BaseChatConsumer):
    async def connect(self):
        await super().connect(is_group=False)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            handler = {
                "typing": lambda d: self.channel_layer.group_send(
                    self.chat_group_name,
                    {"type": "chat.typing", "user": d.get("user"), "username": self.user.username}
                ),
                "ping": lambda d: self.send(json.dumps({"type": "pong"})),
                "edit": self.handle_edit,
                "delete": self.handle_delete,
                "reaction": self.handle_reaction,
            }.get(data.get("type"), self.handle_message)
            await handler(data)
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON", 4000)
        except Exception as e:
            logger.error(f"Error in receive: {str(e)}")
            await self.send_error("Server error", 5000)

    async def handle_message(self, data):
        message_type = data.get("message_type", "text")
        if message_type not in ["text", "image", "video", "file"]:
            return await self.send_error("Invalid message type", 4000)

        # Parse timestamp from client, if provided
        timestamp_str = data.get("timestamp")
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")) if timestamp_str else None

        # Check if a message with the same client-side ID has already been processed
        client_id = data.get("id")
        if client_id and await self.has_processed_message(client_id, self.user.id):
            logger.info(f"Message with client ID {client_id} already processed, skipping")
            return

        message = await self.create_message(
            sender=self.user,
            chat=self.chat_room,
            content=data.get("content", ""),
            message_type=message_type,
            attachment_url=data.get("attachment_url"),
            timestamp=timestamp
        )
        message_data = await self.serialize_message(message)

        # Mark the message as processed for this user
        if client_id:
            await self.mark_message_processed(client_id, self.user.id)

        await self.channel_layer.group_send(
            self.chat_group_name,
            {"type": "chat.message", "message": message_data}
        )
        if data.get("id"):
            await self.send_ack(data["id"], str(message.id))

    async def handle_edit(self, data):
        message_id, content = data.get("message_id"), data.get("content")
        if not (message_id and content):
            return await self.send_error("Missing data", 4000)
        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, sender=self.user
            )
            await database_sync_to_async(message.edit)(content)
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send_error("Message not found or not editable", 4004)

    async def handle_delete(self, data):
        message_id = data.get("message_id")
        if not message_id:
            return await self.send_error("Missing message_id", 4000)
        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, sender=self.user
            )
            await database_sync_to_async(message.delete)()
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send_error("Message not found or not deletable", 4004)

    async def handle_reaction(self, data):
        message_id, emoji = data.get("message_id"), data.get("emoji")
        if not (message_id and emoji):
            return await self.send_error("Missing data", 4000)
        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, chat=self.chat_room
            )
            message.reactions = (message.reactions or []) + [emoji]
            await database_sync_to_async(message.save)(update_fields=["reactions"])
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "chat.reaction", "message_id": message_id, "emoji": emoji}
            )
        except ChatMessage.DoesNotExist:
            await self.send_error("Message not found", 4004)

    async def chat_message(self, event):
        message = event["message"]
        # Check if the message has already been processed for this user
        if await self.has_processed_message(message["id"], self.user.id):
            logger.info(f"Message {message['id']} already processed for user {self.user.id}, skipping")
            return
        await self.mark_message_processed(message["id"], self.user.id)
        await self.send(json.dumps({"message": message}))

    async def chat_typing(self, event):
        await self.send(json.dumps({"type": "typing", "user": event["user"], "username": event["username"]}))

    async def chat_reaction(self, event):
        await self.send(json.dumps({"type": "reaction", "message_id": event["message_id"], "emoji": event["emoji"]}))

class GroupChatConsumer(BaseChatConsumer):
    async def connect(self):
        await super().connect(is_group=True)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            handler = {
                "typing": lambda d: self.channel_layer.group_send(
                    self.chat_group_name,
                    {"type": "group_chat.typing", "user": d.get("user"), "username": self.user.username}
                ),
                "ping": lambda d: self.send(json.dumps({"type": "pong"})),
                "edit": self.handle_edit,
                "delete": self.handle_delete,
                "reaction": self.handle_reaction,
                "pin": self.handle_pin,
                "group_action": self.handle_group_action,
            }.get(data.get("type"), self.handle_message)
            await handler(data)
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON", 4000)
        except Exception as e:
            logger.error(f"Error in receive: {str(e)}")
            await self.send_error("Server error", 5000)

    async def handle_message(self, data):
        message_type = data.get("message_type", "text")
        if message_type not in ["text", "image", "video", "file"]:
            return await self.send_error("Invalid message type", 4000)

        # Parse timestamp from client, if provided
        timestamp_str = data.get("timestamp")
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")) if timestamp_str else None

        # Check if a message with the same client-side ID has already been processed
        client_id = data.get("id")
        if client_id and await self.has_processed_message(client_id, self.user.id):
            logger.info(f"Message with client ID {client_id} already processed, skipping")
            return

        message = await self.create_message(
            sender=self.user,
            chat=self.chat_room,
            content=data.get("content", ""),
            message_type=message_type,
            attachment_url=data.get("attachment_url"),
            timestamp=timestamp
        )
        message_data = await self.serialize_message(message)

        # Mark the message as processed for this user
        if client_id:
            await self.mark_message_processed(client_id, self.user.id)

        await self.channel_layer.group_send(
            self.chat_group_name,
            {"type": "group_chat.message", "message": message_data}
        )
        if data.get("id"):
            await self.send_ack(data["id"], str(message.id))

    async def handle_edit(self, data):
        message_id, content = data.get("message_id"), data.get("content")
        if not (message_id and content):
            return await self.send_error("Missing data", 4000)
        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, sender=self.user
            )
            await database_sync_to_async(message.edit)(content)
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send_error("Message not found or not editable", 4004)

    async def handle_delete(self, data):
        message_id = data.get("message_id")
        if not message_id:
            return await self.send_error("Missing message_id", 4000)
        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, sender=self.user
            )
            await database_sync_to_async(message.delete)()
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send_error("Message not found or not deletable", 4004)

    async def handle_reaction(self, data):
        message_id, emoji = data.get("message_id"), data.get("emoji")
        if not (message_id and emoji):
            return await self.send_error("Missing data", 4000)
        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, chat=self.chat_room
            )
            message.reactions = (message.reactions or []) + [emoji]
            await database_sync_to_async(message.save)(update_fields=["reactions"])
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.reaction", "message_id": message_id, "emoji": emoji}
            )
        except ChatMessage.DoesNotExist:
            await self.send_error("Message not found", 4004)

    async def handle_pin(self, data):
        message_id = data.get("message_id")
        if not message_id:
            return await self.send_error("Missing message_id", 4000)
        try:
            chat_room = await database_sync_to_async(ChatRoom.objects.get)(
                id=self.chat_id, admins=self.user
            )
            message = await database_sync_to_async(ChatMessage.objects.get)(
                id=message_id, chat=chat_room
            )
            chat_room.pinned_message = message
            await database_sync_to_async(chat_room.save)()
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.pin", "message": message_data}
            )
        except (ChatRoom.DoesNotExist, ChatMessage.DoesNotExist):
            await self.send_error("Permission denied or message not found", 4004)

    async def handle_group_action(self, data):
        action, member_id = data.get("action"), data.get("member_id")
        if not (action and member_id):
            return await self.send_error("Missing data", 4000)
        try:
            chat_room = await database_sync_to_async(ChatRoom.objects.get)(
                id=self.chat_id, admins=self.user
            )
            member = await database_sync_to_async(User.objects.get)(id=member_id)
            if action == "add":
                chat_room.members.add(member)
            elif action == "remove":
                chat_room.members.remove(member)
            elif action == "promote":
                chat_room.admins.add(member)
            elif action == "demote":
                chat_room.admins.remove(member)
            else:
                return await self.send_error("Invalid action", 4000)
            await database_sync_to_async(chat_room.save)()
            chat_data = await database_sync_to_async(ChatRoomSerializer(chat_room).data)()
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.update", "chat": chat_data}
            )
        except (ChatRoom.DoesNotExist, User.DoesNotExist):
            await self.send_error("Permission denied or user not found", 4004)

    async def group_chat_message(self, event):
        message = event["message"]
        # Check if the message has already been processed for this user
        if await self.has_processed_message(message["id"], self.user.id):
            logger.info(f"Message {message['id']} already processed for user {self.user.id}, skipping")
            return
        await self.mark_message_processed(message["id"], self.user.id)
        await self.send(json.dumps({"message": message}))

    async def group_chat_typing(self, event):
        await self.send(json.dumps({"type": "typing", "user": event["user"], "username": event["username"]}))

    async def group_chat_pin(self, event):
        await self.send(json.dumps({"type": "pin", "message": event["message"]}))

    async def group_chat_reaction(self, event):
        await self.send(json.dumps({"type": "reaction", "message_id": event["message_id"], "emoji": event["emoji"]}))

    async def group_chat_update(self, event):
        await self.send(json.dumps({"type": "group_update", "chat": event["chat"]}))
class GlobalChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        token = (
            self.scope["query_string"].decode().split("token=")[1]
            if "token=" in self.scope["query_string"].decode()
            else None
        )

        if not token:
            await self.send_error("No token provided", 4001)
            return

        try:
            access_token = AccessToken(token)
            user_id = access_token["user_id"]
            self.user = await self.get_user(user_id)
        except Exception as e:
            await self.send_error(
                str(e), 4003 if isinstance(e, AccessToken.TokenError) else 5000
            )
            return

        self.global_group_name = "global_chat"
        await self.channel_layer.group_add(self.global_group_name, self.channel_name)
        await self.accept()
        logger.info(f"User {self.user.username} connected to global chat")

    async def disconnect(self, close_code):
        if hasattr(self, "global_group_name"):
            await self.channel_layer.group_discard(self.global_group_name, self.channel_name)
        logger.info(f"Disconnected from global chat with code {close_code}")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get("type") == "ping":
                await self.send(json.dumps({"type": "pong"}))
            else:
                logger.warn(f"Unknown message type in global chat: {data.get('type')}")
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON", 4000)
        except Exception as e:
            logger.error(f"Error in global chat receive: {str(e)}")
            await self.send_error("Server error", 5000)

    async def send_error(self, message, code):
        await self.send(json.dumps({"error": message}))
        await self.close(code=code)

    @database_sync_to_async
    def get_user(self, user_id):
        return User.objects.get(id=user_id)

    async def global_message(self, event):
        await self.send(json.dumps({"message": event["message"]}))