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
        is_member = await database_sync_to_async(Group.objects.filter)(
            id=self.group_id, members=self.user
        ).aexists()
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

            group = await database_sync_to_async(Group.objects.get)(id=self.group_id)
            if group.admin != self.user:
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": "Only admins can send messages"
                }))
                return

            group_message = await database_sync_to_async(GroupMessage.objects.create)(
                group=group,
                sender=self.user,
                message=message,
                attachment=attachment
            )
            message_data = await database_sync_to_async(GroupMessageSerializer)(group_message).data

            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "group_message",
                    "message": message_data
                }
            )

    async def group_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_message",
            "message": event['message']
        }))