import json
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

        self.group_id = self.scope['url_route']['kwargs']['group_id']
        self.group_name = f"group_{self.group_id}"

        # Check if user is a member of the group
        is_member = await database_sync_to_async(lambda: Group.objects.filter(
            id=self.group_id, members=self.user
        ).exists())()
        if not is_member:
            await self.close(code=4004)  # Forbidden: Not a group member
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type')

        if message_type == 'group_message':
            message = data.get('message')
            attachment = data.get('attachment', None)

            group = await database_sync_to_async(lambda: Group.objects.get(id=self.group_id))()

            # Create the group message (removed admin check)
            group_message = await database_sync_to_async(GroupMessage.objects.create)(
                group=group,
                sender=self.user,
                message=message,
                attachment=attachment
            )

            # Mark the message as read by the sender
            await database_sync_to_async(group_message.read_by.add)(self.user)

            message_data = await database_sync_to_async(lambda: GroupMessageSerializer(group_message).data)()

            await self.channel_layer.group_send(
                self.group_name,
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

            await self.channel_layer.group_send(
                self.group_name,
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

            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "group_message",
                    "message": message_data
                }
            )

        elif message_type == 'typing':
            await self.channel_layer.group_send(
                self.group_name,
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
            "user_id": event['user_id']
        }))

class GlobalGroupsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Extract token from query string
        token = self.scope['query_string'].decode().split('token=')[1] if 'token=' in self.scope['query_string'].decode() else None
        if not token:
            await self.close(code=4001)  # Unauthorized: No token provided
            return

        # Validate token and authenticate user
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

        # Get all groups the user is a member of
        self.groups = await database_sync_to_async(self.get_user_groups)()
        if not self.groups:
            await self.close(code=4005)  # No groups found for the user
            return

        # Join the user to each group's channel layer group
        self.group_names = [f"group_{group.id}" for group in self.groups]
        for group_name in self.group_names:
            await self.channel_layer.group_add(group_name, self.channel_name)

        await self.accept()
        print(f"GlobalGroupsConsumer connected for user {self.user.id}")

    @database_sync_to_async
    def get_user_groups(self):
        return list(Group.objects.filter(members=self.user))

    async def disconnect(self, close_code):
        # Leave all group channels on disconnect
        for group_name in getattr(self, 'group_names', []):
            await self.channel_layer.group_discard(group_name, self.channel_name)
        print(f"GlobalGroupsConsumer disconnected for user {self.user.id} with code {close_code}")

    async def group_message(self, event):
        # Forward group messages to the client
        message = event['message']
        await self.send(text_data=json.dumps({
            "type": "group_message",
            "message": message
        }))