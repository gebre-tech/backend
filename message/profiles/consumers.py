# profiles/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone
from .models import Profile

class ProfileConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        
    async def disconnect(self, close_code):
        pass
        
    async def receive(self, text_data):
        data = json.loads(text_data)
        if data.get('type') == 'update_last_seen':
            profile = await Profile.objects.get(user=self.scope['user'])
            profile.last_seen = timezone.now()
            await profile.asave()
            
            await self.send(text_data=json.dumps({
                'type': 'last_seen_update',
                'last_seen': profile.last_seen.isoformat()
            }))


