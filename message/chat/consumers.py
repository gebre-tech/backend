import json
import logging
from typing import Optional  # For type hints in comments
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatMessage, ChatRoom
from .serializers import ChatMessageSerializer, ChatRoomSerializer
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist

User = get_user_model()
logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Handle WebSocket connection with robust authentication."""
        self.chat_id = self.scope["url_route"]["kwargs"]["chat_id"]  # type: str
        self.chat_group_name = f"chat_{self.chat_id}"

        query_string = self.scope["query_string"].decode()
        token = query_string.split("token=")[1] if "token=" in query_string else None
        logger.debug(f"ChatConsumer: Connecting to chat {self.chat_id} with token: {token[:10] if token else 'None'}...")

        if not token:
            logger.warning("No authentication token provided")
            await self.close(code=4001)
            return

        try:
            access_token = AccessToken(token)
            user_id = access_token["user_id"]  # type: int
            self.user = await database_sync_to_async(User.objects.get)(id=user_id)
            self.chat_room = await database_sync_to_async(ChatRoom.objects.prefetch_related("members").get)(id=self.chat_id)
            # Correctly await the filtered queryset before calling exists()
            members_query = await database_sync_to_async(self.chat_room.members.filter)(id=self.user.id)
            member_exists = await database_sync_to_async(lambda: members_query.exists())()
            if not member_exists:
                logger.warning(f"User {user_id} not authorized for chat {self.chat_id}")
                await self.close(code=4002)
                return
            if self.chat_room.is_group:
                logger.warning(f"Chat {self.chat_id} is a group chat, rejecting")
                await self.close(code=4005)
                return
        except TokenError as e:
            logger.error(f"Token validation failed: {str(e)}")
            await self.close(code=4003)
            return
        except User.DoesNotExist:
            logger.error(f"User {user_id} not found")
            await self.close(code=4003)
            return
        except ChatRoom.DoesNotExist:
            logger.error(f"Chat room {self.chat_id} not found")
            await self.close(code=4004)
            return
        except Exception as e:
            logger.error(f"Unexpected error in connect: {str(e)}", exc_info=True)
            await self.close(code=5000)
            return

        # Accept connection only after all checks pass
        await self.accept()
        await self.channel_layer.group_add(self.chat_group_name, self.channel_name)
        logger.info(f"User {self.user.username} connected to chat {self.chat_id}")

    async def disconnect(self, close_code):
        """Clean up on disconnect."""
        if hasattr(self, "chat_group_name"):
            await self.channel_layer.group_discard(self.chat_group_name, self.channel_name)
        logger.info(f"Disconnected from chat {self.chat_id} with code {close_code}")

    async def receive(self, text_data):
        """Process incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            message_type = data.get("type")
            handler = {
                "typing": self.handle_typing,
                "ping": self.handle_ping,
                "edit": self.handle_edit,
                "delete": self.handle_delete,
            }.get(message_type, self.handle_message)
            await handler(data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"error": "Invalid message format"}))
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}", exc_info=True)
            await self.send(text_data=json.dumps({"error": f"Message processing failed: {str(e)}"}))

    async def handle_message(self, data):
        """Handle new chat messages."""
        content = data.get("content", "")
        message_type = data.get("message_type", "text")
        attachment_url = data.get("attachment_url")
        client_message_id = data.get("id")
        forward_id = data.get("forward_id")

        forwarded_from = await self.get_forwarded_message(forward_id) if forward_id else None

        message = await database_sync_to_async(ChatMessage.objects.create)(
            sender=self.user,
            chat=self.chat_room,
            content=content,
            message_type=message_type,
            attachment=attachment_url,
            forwarded_from=forwarded_from
        )
        await database_sync_to_async(message.delivered_to.add)(*self.chat_room.members.all())
        message_data = await self.serialize_message(message)

        await self.channel_layer.group_send(
            self.chat_group_name,
            {"type": "chat.message", "message": message_data}
        )

        if client_message_id:
            await self.send_ack(client_message_id, str(message.id))

    async def handle_typing(self, data):
        """Broadcast typing indicators."""
        await self.channel_layer.group_send(
            self.chat_group_name,
            {"type": "chat.typing", "user": data.get("user"), "username": self.user.username}
        )

    async def handle_ping(self, data):
        """Respond to ping with pong."""
        await self.send(text_data=json.dumps({"type": "pong"}))

    async def handle_edit(self, data):
        """Edit an existing message."""
        message_id = data.get("message_id")
        new_content = data.get("content")
        if not message_id or not new_content:
            await self.send(text_data=json.dumps({"error": "Message ID and content required"}))
            return

        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(id=message_id, sender=self.user)
            await database_sync_to_async(message.edit)(new_content)
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Message not found or unauthorized"}))

    async def handle_delete(self, data):
        """Delete a message."""
        message_id = data.get("message_id")
        if not message_id:
            await self.send(text_data=json.dumps({"error": "Message ID required"}))
            return

        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(id=message_id, sender=self.user)
            await database_sync_to_async(message.delete)()
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Message not found or unauthorized"}))

    async def chat_message(self, event):
        """Broadcast message to client."""
        await self.send(text_data=json.dumps({"message": event["message"]}))

    async def chat_typing(self, event):
        """Broadcast typing event to client."""
        await self.send(text_data=json.dumps({"type": "typing", "user": event["user"], "username": event["username"]}))

    @database_sync_to_async
    def get_forwarded_message(self, forward_id: str) -> Optional[ChatMessage]:
        """Fetch forwarded message if exists."""
        try:
            return ChatMessage.objects.get(id=forward_id)
        except ObjectDoesNotExist:
            logger.warning(f"Forwarded message {forward_id} not found")
            return None

    @database_sync_to_async
    def serialize_message(self, message: ChatMessage) -> dict:
        """Serialize a message efficiently."""
        return ChatMessageSerializer(message).data

    async def send_ack(self, client_id: str, server_id: str):
        """Send acknowledgment to client."""
        await self.send(text_data=json.dumps({"type": "ack", "messageId": client_id, "serverId": server_id}))

class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Handle group chat WebSocket connection."""
        self.chat_id = self.scope["url_route"]["kwargs"]["chat_id"]  # type: str
        self.chat_group_name = f"group_chat_{self.chat_id}"

        query_string = self.scope["query_string"].decode()
        token = query_string.split("token=")[1] if "token=" in query_string else None
        logger.debug(f"GroupChatConsumer: Connecting to group chat {self.chat_id} with token: {token[:10] if token else 'None'}...")

        if not token:
            logger.warning("No authentication token provided")
            await self.close(code=4001)
            return

        try:
            access_token = AccessToken(token)
            user_id = access_token["user_id"]  # type: int
            self.user = await database_sync_to_async(User.objects.get)(id=user_id)
            self.chat_room = await database_sync_to_async(ChatRoom.objects.prefetch_related("members").get)(id=self.chat_id)
            # Correctly await the filtered queryset before calling exists()
            members_query = await database_sync_to_async(self.chat_room.members.filter)(id=self.user.id)
            member_exists = await database_sync_to_async(lambda: members_query.exists())()
            if not member_exists:
                logger.warning(f"User {user_id} not authorized for group chat {self.chat_id}")
                await self.close(code=4002)
                return
            if not self.chat_room.is_group:
                logger.warning(f"Chat {self.chat_id} is not a group chat, rejecting")
                await self.close(code=4006)
                return
        except TokenError as e:
            logger.error(f"Token validation failed: {str(e)}")
            await self.close(code=4003)
            return
        except User.DoesNotExist:
            logger.error(f"User {user_id} not found")
            await self.close(code=4003)
            return
        except ChatRoom.DoesNotExist:
            logger.error(f"Group chat {self.chat_id} not found")
            await self.close(code=4004)
            return
        except Exception as e:
            logger.error(f"Unexpected error in connect: {str(e)}", exc_info=True)
            await self.close(code=5000)
            return

        # Accept connection only after all checks pass
        await self.accept()
        await self.channel_layer.group_add(self.chat_group_name, self.channel_name)
        logger.info(f"User {self.user.username} connected to group chat {self.chat_id}")

    async def disconnect(self, close_code):
        """Clean up on disconnect."""
        if hasattr(self, "chat_group_name"):
            await self.channel_layer.group_discard(self.chat_group_name, self.channel_name)
        logger.info(f"Disconnected from group chat {self.chat_id} with code {close_code}")

    async def receive(self, text_data):
        """Process incoming WebSocket messages for group chats."""
        try:
            data = json.loads(text_data)
            message_type = data.get("type")
            handler = {
                "typing": self.handle_typing,
                "ping": self.handle_ping,
                "edit": self.handle_edit,
                "delete": self.handle_delete,
                "pin": self.handle_pin,
                "group_action": self.handle_group_action,
            }.get(message_type, self.handle_message)
            await handler(data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"error": "Invalid message format"}))
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}", exc_info=True)
            await self.send(text_data=json.dumps({"error": f"Message processing failed: {str(e)}"}))

    async def handle_message(self, data):
        """Handle new group chat messages."""
        content = data.get("content", "")
        message_type = data.get("message_type", "text")
        attachment_url = data.get("attachment_url")
        client_message_id = data.get("id")
        forward_id = data.get("forward_id")

        forwarded_from = await self.get_forwarded_message(forward_id) if forward_id else None

        message = await database_sync_to_async(ChatMessage.objects.create)(
            sender=self.user,
            chat=self.chat_room,
            content=content,
            message_type=message_type,
            attachment=attachment_url,
            forwarded_from=forwarded_from
        )
        await database_sync_to_async(message.delivered_to.add)(*self.chat_room.members.all())
        message_data = await self.serialize_message(message)

        await self.channel_layer.group_send(
            self.chat_group_name,
            {"type": "group_chat.message", "message": message_data}
        )

        if client_message_id:
            await self.send_ack(client_message_id, str(message.id))

    async def handle_typing(self, data):
        """Broadcast typing indicators."""
        await self.channel_layer.group_send(
            self.chat_group_name,
            {"type": "group_chat.typing", "user": data.get("user"), "username": self.user.username}
        )

    async def handle_ping(self, data):
        """Respond to ping with pong."""
        await self.send(text_data=json.dumps({"type": "pong"}))

    async def handle_edit(self, data):
        """Edit an existing message."""
        message_id = data.get("message_id")
        new_content = data.get("content")
        if not message_id or not new_content:
            await self.send(text_data=json.dumps({"error": "Message ID and content required"}))
            return

        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(id=message_id, sender=self.user)
            await database_sync_to_async(message.edit)(new_content)
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Message not found or unauthorized"}))

    async def handle_delete(self, data):
        """Delete a message."""
        message_id = data.get("message_id")
        if not message_id:
            await self.send(text_data=json.dumps({"error": "Message ID required"}))
            return

        try:
            message = await database_sync_to_async(ChatMessage.objects.get)(id=message_id, sender=self.user)
            await database_sync_to_async(message.delete)()
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.message", "message": message_data}
            )
        except ChatMessage.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Message not found or unauthorized"}))

    async def handle_pin(self, data):
        """Pin a message in the group chat."""
        message_id = data.get("message_id")
        if not message_id:
            await self.send(text_data=json.dumps({"error": "Message ID required"}))
            return

        try:
            chat_room = await database_sync_to_async(ChatRoom.objects.get)(id=self.chat_id, admins=self.user)
            message = await database_sync_to_async(ChatMessage.objects.get)(id=message_id, chat=chat_room)
            await database_sync_to_async(setattr)(chat_room, "pinned_message", message)
            await database_sync_to_async(chat_room.save)()
            message_data = await self.serialize_message(message)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.pin", "message": message_data}
            )
        except ChatRoom.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Chat not found or unauthorized"}))
        except ChatMessage.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Message not found"}))

    async def handle_group_action(self, data):
        """Handle group admin actions."""
        action = data.get("action")
        member_id = data.get("member_id")
        if not action or not member_id:
            await self.send(text_data=json.dumps({"error": "Action and member ID required"}))
            return

        try:
            chat_room = await database_sync_to_async(ChatRoom.objects.get)(id=self.chat_id, admins=self.user)
            member = await database_sync_to_async(User.objects.get)(id=member_id)
            action_map = {
                "add": chat_room.add_member,
                "remove": chat_room.remove_member,
                "promote": lambda m, u: chat_room.admins.add(m) or self.create_system_message(chat_room, f"{m.username} was promoted to admin."),
                "demote": lambda m, u: chat_room.admins.remove(m) or self.create_system_message(chat_room, f"{m.username} was demoted from admin."),
            }
            if action not in action_map:
                await self.send(text_data=json.dumps({"error": "Invalid action"}))
                return

            await database_sync_to_async(action_map[action])(member, self.user)
            chat_data = await self.serialize_chat_room(chat_room)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {"type": "group_chat.update", "chat": chat_data}
            )
        except ChatRoom.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Chat not found or unauthorized"}))
        except User.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "Member not found"}))

    async def group_chat_message(self, event):
        """Broadcast message to client."""
        await self.send(text_data=json.dumps({"message": event["message"]}))

    async def group_chat_typing(self, event):
        """Broadcast typing event to client."""
        await self.send(text_data=json.dumps({"type": "typing", "user": event["user"], "username": event["username"]}))

    async def group_chat_pin(self, event):
        """Broadcast pin event to client."""
        await self.send(text_data=json.dumps({"type": "pin", "message": event["message"]}))

    async def group_chat_update(self, event):
        """Broadcast group update to client."""
        await self.send(text_data=json.dumps({"type": "group_update", "chat": event["chat"]}))

    @database_sync_to_async
    def get_forwarded_message(self, forward_id: str) -> Optional[ChatMessage]:
        """Fetch forwarded message if exists."""
        try:
            return ChatMessage.objects.get(id=forward_id)
        except ObjectDoesNotExist:
            logger.warning(f"Forwarded message {forward_id} not found")
            return None

    @database_sync_to_async
    def serialize_message(self, message: ChatMessage) -> dict:
        """Serialize a message efficiently."""
        return ChatMessageSerializer(message).data

    @database_sync_to_async
    def serialize_chat_room(self, chat_room: ChatRoom) -> dict:
        """Serialize a chat room efficiently."""
        return ChatRoomSerializer(chat_room).data

    @database_sync_to_async
    def create_system_message(self, chat_room: ChatRoom, content: str) -> None:
        """Create a system message."""
        ChatMessage.objects.create(chat=chat_room, sender=self.user, content=content, message_type="system")

    async def send_ack(self, client_id: str, server_id: str):
        """Send acknowledgment to client."""
        await self.send(text_data=json.dumps({"type": "ack", "messageId": client_id, "serverId": server_id}))