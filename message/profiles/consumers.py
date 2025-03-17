# profiles/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from .models import Profile
from authentication.models import User

class ProfileConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        self.group_name = f"profile_{self.user.id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('type')

        if action == 'update_last_seen':
            await self.update_last_seen()
        elif action == 'update_profile':
            await self.update_profile(data)

    @database_sync_to_async
    def update_last_seen(self):
        profile = Profile.objects.get(user=self.user)
        profile.last_seen = timezone.now()
        profile.save()
        return profile.last_seen.isoformat()

    @database_sync_to_async
    def update_profile(self, data):
        profile = Profile.objects.get(user=self.user)
        user = profile.user
        user.username = data.get('username', user.username)
        user.first_name = data.get('first_name', user.first_name)
        user.last_name = data.get('last_name', user.last_name)
        user.save()
        profile.bio = data.get('bio', profile.bio)
        if 'profile_picture' in data and data['profile_picture']:
            # For simplicity, assume profile_picture is handled via URL or file upload in view
            profile.profile_picture = data['profile_picture']
        profile.save()
        return {
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'bio': profile.bio,
            'profile_picture': profile.profile_picture.url if profile.profile_picture else None,
            'last_seen': profile.last_seen.isoformat()
        }

    async def profile_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'profile_update',
            'username': event['username'],
            'first_name': event['first_name'],
            'last_name': event['last_name'],
            'bio': event['bio'],
            'profile_picture': event['profile_picture'],
            'last_seen': event['last_seen']
        }))

    async def update_last_seen(self):
        last_seen = await self.update_last_seen()
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'last_seen_update',
                'last_seen': last_seen
            }
        )

    async def update_profile(self, data):
        updated_data = await self.update_profile(data)
        await self.channel_layer.group_send(
            self.group_name,
            {
                'type': 'profile_update',
                'username': updated_data['username'],
                'first_name': updated_data['first_name'],
                'last_name': updated_data['last_name'],
                'bio': updated_data['bio'],
                'profile_picture': updated_data['profile_picture'],
                'last_seen': updated_data['last_seen']
            }
        )

    async def last_seen_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'last_seen_update',
            'last_seen': event['last_seen']
        }))