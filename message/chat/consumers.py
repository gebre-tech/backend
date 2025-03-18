import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatMessage, ChatRoom
from .serializers import ChatMessageSerializer
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.chat_id = self.scope["url_route"]["kwargs"]["chat_id"]
        self.chat_group_name = f"chat_{self.chat_id}"

        query_string = self.scope['query_string'].decode()
        token = query_string.split('token=')[1] if 'token=' in query_string else None
        logger.info(f"ChatConsumer: Connect attempt for chat {self.chat_id} with token: {token[:10]}...")

        if not token:
            logger.warning("No token provided")
            await self.close(code=4001)
            return

        try:
            access_token = AccessToken(token)
            self.user = await database_sync_to_async(User.objects.get)(id=access_token['user_id'])
            logger.info(f"User authenticated: {self.user.username}")
            
            self.chat_room = await database_sync_to_async(ChatRoom.objects.prefetch_related('members').get)(id=self.chat_id)
            if not await database_sync_to_async(self.chat_room.members.filter)(id=self.user.id).exists():
                logger.warning(f"User {self.user.username} not in chat room {self.chat_id}")
                await self.close(code=4002)
                return
            if self.chat_room.is_group:
                logger.warning(f"Chat {self.chat_id} is a group chat; use GroupChatConsumer")
                await self.close(code=4005)  # Redirect to group consumer if needed
                return
        except ChatRoom.DoesNotExist:
            logger.error(f"Chat room {self.chat_id} does not exist")
            await self.close(code=4004)
            return
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            await self.close(code=4003)
            return

        logger.info(f"ChatConsumer: Connection accepted for user {self.user.username} in chat {self.chat_id}")
        await self.channel_layer.group_add(self.chat_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        logger.info(f"ChatConsumer: Disconnected for chat {self.chat_id} with code {close_code}")
        await self.channel_layer.group_discard(self.chat_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            logger.debug(f"ChatConsumer: Received message: {data}")
            
            if data.get("type") == "typing":
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {"type": "chat.typing", "user": data["user"]}
                )
            else:
                content = data.get("content")
                message_type = data.get("message_type", "text")
                attachment_url = data.get("attachment_url")

                message = await database_sync_to_async(ChatMessage.objects.create)(
                    sender=self.user,
                    chat=self.chat_room,
                    content=content,
                    message_type=message_type,
                    attachment=attachment_url if attachment_url else None
                )
                
                await database_sync_to_async(message.delivered_to.add)(*self.chat_room.members.all())
                serializer = await database_sync_to_async(ChatMessageSerializer)(message, context={'request': None})
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {"type": "chat.message", "message": serializer.data}
                )
        except Exception as e:
            logger.error(f"ChatConsumer: Error processing message: {str(e)}")
            await self.send(text_data=json.dumps({"error": "Failed to process message"}))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def chat_typing(self, event):
        await self.send(text_data=json.dumps({"type": "typing", "user": event["user"]}))

class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.chat_id = self.scope["url_route"]["kwargs"]["chat_id"]
        self.chat_group_name = f"group_chat_{self.chat_id}"

        query_string = self.scope['query_string'].decode()
        token = query_string.split('token=')[1] if 'token=' in query_string else None
        logger.info(f"GroupChatConsumer: Connect attempt for group chat {self.chat_id} with token: {token[:10]}...")

        if not token:
            logger.warning("No token provided")
            await self.close(code=4001)
            return

        try:
            access_token = AccessToken(token)
            self.user = await database_sync_to_async(User.objects.get)(id=access_token['user_id'])
            logger.info(f"User authenticated: {self.user.username}")
            
            self.chat_room = await database_sync_to_async(ChatRoom.objects.prefetch_related('members').get)(id=self.chat_id)
            if not await database_sync_to_async(self.chat_room.members.filter)(id=self.user.id).exists():
                logger.warning(f"User {self.user.username} not in group chat {self.chat_id}")
                await self.close(code=4002)
                return
            if not self.chat_room.is_group:
                logger.warning(f"Chat {self.chat_id} is not a group chat; use ChatConsumer")
                await self.close(code=4006)  # Redirect to individual consumer if needed
                return
        except ChatRoom.DoesNotExist:
            logger.error(f"Group chat {self.chat_id} does not exist")
            await self.close(code=4004)
            return
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            await self.close(code=4003)
            return

        logger.info(f"GroupChatConsumer: Connection accepted for user {self.user.username} in group chat {self.chat_id}")
        await self.channel_layer.group_add(self.chat_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        logger.info(f"GroupChatConsumer: Disconnected for group chat {self.chat_id} with code {close_code}")
        await self.channel_layer.group_discard(self.chat_group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            logger.debug(f"GroupChatConsumer: Received message: {data}")
            
            if data.get("type") == "typing":
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {"type": "group_chat.typing", "user": data["user"]}
                )
            else:
                content = data.get("content")
                message_type = data.get("message_type", "text")
                attachment_url = data.get("attachment_url")

                message = await database_sync_to_async(ChatMessage.objects.create)(
                    sender=self.user,
                    chat=self.chat_room,
                    content=content,
                    message_type=message_type,
                    attachment=attachment_url if attachment_url else None
                )
                
                await database_sync_to_async(message.delivered_to.add)(*self.chat_room.members.all())
                serializer = await database_sync_to_async(ChatMessageSerializer)(message, context={'request': None})
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {"type": "group_chat.message", "message": serializer.data}
                )
        except Exception as e:
            logger.error(f"GroupChatConsumer: Error processing message: {str(e)}")
            await self.send(text_data=json.dumps({"error": "Failed to process message"}))

    async def group_chat_message(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def group_chat_typing(self, event):
        await self.send(text_data=json.dumps({"type": "typing", "user": event["user"]}))

class ContactsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        query_string = self.scope['query_string'].decode()
        token = query_string.split('token=')[1] if 'token=' in query_string else None
        logger.info(f"ContactsConsumer: Connect attempt with token: {token[:10]}...")

        if not token:
            logger.warning("No token provided")
            await self.close(code=4001)
            return

        try:
            access_token = AccessToken(token)
            self.user = await database_sync_to_async(User.objects.get)(id=access_token['user_id'])
            logger.info(f"User authenticated: {self.user.username}")
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            await self.close(code=4003)
            return

        self.group_name = "contacts"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        logger.info(f"ContactsConsumer: Disconnected with code {close_code}")
        await self.channel_layer.group_discard(self.group_name, self.channel_name)