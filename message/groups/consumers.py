import json
import re
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Group, GroupMessage
from authentication.models import User
from .serializers import GroupMessageSerializer
from rest_framework_simplejwt.tokens import AccessToken

class GroupChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        token = self.scope['query_string'].decode().split('token=')[1] if 'token=' in self.scope['query_string'].decode() else None
        if not token:
            await self.close(code=4001)  # Unauthorized: No token provided
            return

        try:
            access_token = AccessToken(token)
            user_id = access_token['user_id']
            self.user = await database_sync_to_async(User.objects.get)(id=user_id)
            if not self.user.is_authenticated:
                await self.close(code=4002)  # Unauthorized: User not authenticated
                return
        except Exception as e:
            print(f"Token validation error: {str(e)}")
            await self.close(code=4003)  # Forbidden: Invalid token
            return

        self.group_id = self.scope['url_route']['kwargs'].get('group_id')
        self.group_channels = []

        if self.group_id:
            is_member = await database_sync_to_async(lambda: Group.objects.filter(
                id=self.group_id, members=self.user
            ).exists())()
            if not is_member:
                await self.close(code=4004)  # Forbidden: Not a group member
                return
            group_channel = f"group_{self.group_id}"
            if not self._is_valid_group_name(group_channel):
                print(f"Invalid group channel name: {group_channel}")
                await self.close(code=4006)  # Invalid group channel name
                return
            self.group_channels = [group_channel]
        else:
            self.groups = await self.get_user_groups()
            if not self.groups:
                await self.close(code=4005)  # No groups found for the user
                return
            self.group_channels = []
            for group in self.groups:
                group_channel = f"group_{group.id}"
                if self._is_valid_group_name(group_channel):
                    self.group_channels.append(group_channel)
                else:
                    print(f"Skipping invalid group channel name: {group_channel}")

        for group_channel in self.group_channels:
            await self.channel_layer.group_add(group_channel, self.channel_name)

        await self.accept()
        print(f"GroupChatConsumer connected for user {self.user.id}")

    @database_sync_to_async
    def get_user_groups(self):
        return list(Group.objects.filter(members=self.user))

    def _is_valid_group_name(self, name):
        """
        Validate that the group name adheres to Channels' naming rules:
        - Must be a Unicode string
        - Length < 100 characters
        - Contains only ASCII alphanumerics, hyphens, underscores, or periods
        """
        if not isinstance(name, str):
            return False
        if len(name) >= 100:
            return False
        # Channels' valid group name regex: only ASCII alphanumerics, hyphens, underscores, periods
        pattern = r'^[a-zA-Z0-9\-_\.]+$'
        return bool(re.match(pattern, name))

    async def disconnect(self, close_code):
        for group_channel in self.group_channels:
            if self._is_valid_group_name(group_channel):
                await self.channel_layer.group_discard(group_channel, self.channel_name)
            else:
                print(f"Cannot discard invalid group channel name: {group_channel}")
        print(f"GroupChatConsumer disconnected for user {self.user.id} with code {close_code}")

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type')

        if message_type == 'group_message':
            message = data.get('message')
            attachment = data.get('attachment', None)
            group_id = data.get('group_id', self.group_id)
            if not group_id:
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "No group_id provided"
                }))
                return

            group = await database_sync_to_async(lambda: Group.objects.get(id=group_id))()
            group_channel = f"group_{group_id}"
            if not self._is_valid_group_name(group_channel):
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "Invalid group channel name"
                }))
                return

            group_message = await database_sync_to_async(GroupMessage.objects.create)(
                group=group,
                sender=self.user,
                message=message,
                attachment=attachment
            )

            await database_sync_to_async(group_message.read_by.add)(self.user)

            message_data = await database_sync_to_async(lambda: GroupMessageSerializer(group_message).data)()

            await self.channel_layer.group_send(
                group_channel,
                {
                    "type": "group_message",
                    "message": message_data
                }
            )

        elif message_type == 'reaction':
            message_id = data.get('message_id')
            reaction = data.get('reaction')

            group_message = await database_sync_to_async(GroupMessage.objects.get)(id=message_id)
            reactions = group_message.reactions or {}
            reactions[str(self.user.id)] = reaction
            group_message.reactions = reactions
            await database_sync_to_async(group_message.save)()

            message_data = await database_sync_to_async(lambda: GroupMessageSerializer(group_message).data)()

            group_channel = f"group_{group_message.group.id}"
            if not self._is_valid_group_name(group_channel):
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "Invalid group channel name"
                }))
                return

            await self.channel_layer.group_send(
                group_channel,
                {
                    "type": "group_message",
                    "message": message_data
                }
            )

        elif message_type == 'read_receipt':
            message_id = data.get('message_id')
            group_message = await database_sync_to_async(GroupMessage.objects.get)(id=message_id)
            await database_sync_to_async(group_message.read_by.add)(self.user)

            message_data = await database_sync_to_async(lambda: GroupMessageSerializer(group_message).data)()

            group_channel = f"group_{group_message.group.id}"
            if not self._is_valid_group_name(group_channel):
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "Invalid group channel name"
                }))
                return

            await self.channel_layer.group_send(
                group_channel,
                {
                    "type": "group_message",
                    "message": message_data
                }
            )

        elif message_type == 'typing':
            group_id = data.get('group_id', self.group_id)
            if not group_id:
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "No group_id provided"
                }))
                return

            group_channel = f"group_{group_id}"
            if not self._is_valid_group_name(group_channel):
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "Invalid group channel name"
                }))
                return

            await self.channel_layer.group_send(
                group_channel,
                {
                    "type": "typing",
                    "user_id": self.user.id,
                    "first_name": self.user.first_name,
                }
            )

    async def group_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_message",
            "message": event['message']
        }))

    async def typing(self, event):
        await self.send(text_data=json.dumps({
            "type": "typing",
            "user_id": event['user_id'],
            "first_name": event['first_name']
        }))