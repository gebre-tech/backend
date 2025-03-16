# contacts/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import FriendRequest, Contact
from authentication.models import User

class ContactConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
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
            friend_request = await database_sync_to_async(FriendRequest.objects.create)(
                sender=self.user, receiver=receiver
            )
            # Notify sender
            await self.channel_layer.group_send(
                f"user_{self.user.id}",
                {
                    'type': 'friend_request_sent',
                    'request': {
                        'id': friend_request.id,
                        'receiver': {'username': receiver.username},
                    },
                }
            )
            # Notify receiver
            await self.channel_layer.group_send(
                f"user_{receiver.id}",
                {
                    'type': 'friend_request',
                    'request': {
                        'id': friend_request.id,
                        'sender': {'username': self.user.username},
                    },
                }
            )
        elif message_type == 'friend_request_accepted':
            request_id = data.get('requestId')
            friend_request = await database_sync_to_async(FriendRequest.objects.get)(id=request_id)
            sender = friend_request.sender
            receiver = self.user
            friend_request.accepted = True
            await database_sync_to_async(friend_request.save)()
            await database_sync_to_async(Contact.objects.get_or_create)(user=sender, friend=receiver)
            await database_sync_to_async(Contact.objects.get_or_create)(user=receiver, friend=sender)
            # Notify sender
            await self.channel_layer.group_send(
                f"user_{sender.id}",
                {'type': 'friend_request_accepted', 'requestId': request_id}
            )
            # Notify receiver
            await self.channel_layer.group_send(
                f"user_{receiver.id}",
                {'type': 'friend_request_accepted', 'requestId': request_id}
            )
        elif message_type == 'friend_request_rejected':
            request_id = data.get('requestId')
            friend_request = await database_sync_to_async(FriendRequest.objects.get)(id=request_id)
            sender = friend_request.sender
            await database_sync_to_async(friend_request.delete)()
            # Notify sender
            await self.channel_layer.group_send(
                f"user_{sender.id}",
                {'type': 'friend_request_rejected', 'requestId': request_id}
            )
            # Notify receiver (optional, but included for consistency)
            await self.channel_layer.group_send(
                f"user_{self.user.id}",
                {'type': 'friend_request_rejected', 'requestId': request_id}
            )

    async def friend_request(self, event):
        await self.send(text_data=json.dumps({
            'type': 'friend_request',
            'request': event['request'],
        }))

    async def friend_request_sent(self, event):
        await self.send(text_data=json.dumps({
            'type': 'friend_request_sent',
            'request': event['request'],
        }))

    async def friend_request_accepted(self, event):
        await self.send(text_data=json.dumps({
            'type': 'friend_request_accepted',
            'requestId': event['requestId'],
        }))

    async def friend_request_rejected(self, event):
        await self.send(text_data=json.dumps({
            'type': 'friend_request_rejected',
            'requestId': event['requestId'],
        }))