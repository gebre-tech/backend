import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import FriendRequest, Contact
from authentication.models import User
from .serializers import FriendRequestSerializer, ContactSerializer
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model

User = get_user_model()

class ContactConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Extract token from query string
        token = self.scope['query_string'].decode().split('token=')[1] if 'token=' in self.scope['query_string'].decode() else None
        if not token:
            await self.close(code=4001)  # Unauthorized: No token provided
            return

        try:
            # Validate token and set user
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

        self.group_name = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_type = data.get('type')

        if message_type == 'friend_request':
            username = data.get('username')
            receiver = await database_sync_to_async(User.objects.get)(username=username)
            friend_request = await database_sync_to_async(FriendRequest.objects.create)(sender=self.user, receiver=receiver)
            request_data = await database_sync_to_async(FriendRequestSerializer)(friend_request).data
            await self.channel_layer.group_send(
                f"user_{self.user.id}",
                {"type": "friend_request_sent", "request": request_data}
            )
            await self.channel_layer.group_send(
                f"user_{receiver.id}",
                {"type": "friend_request_received", "request": request_data}
            )

    async def friend_request_received(self, event):
        await self.send(text_data=json.dumps(event))

    async def friend_request_sent(self, event):
        await self.send(text_data=json.dumps(event))

    async def friend_request_accepted(self, event):
        await self.send(text_data=json.dumps(event))

    async def friend_request_rejected(self, event):
        await self.send(text_data=json.dumps(event))

    async def friend_removed(self, event):
        await self.send(text_data=json.dumps(event))