import json
import re
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Group, GroupMessage
from authentication.models import User
from .serializers import GroupMessageSerializer
from rest_framework_simplejwt.tokens import AccessToken
from django.core.files.base import ContentFile
from django.conf import settings
from datetime import datetime

class GroupChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_metadata = None

    async def connect(self):
        token = self.scope['query_string'].decode().split('token=')[1] if 'token=' in self.scope['query_string'].decode() else None
        if not token:
            await self.close(code=4001)
            return

        try:
            access_token = AccessToken(token)
            user_id = access_token['user_id']
            self.user = await database_sync_to_async(User.objects.get)(id=user_id)
            if not self.user.is_authenticated:
                await self.close(code=4002)
                return
        except Exception as e:
            print(f"Token validation error: {str(e)}")
            await self.close(code=4003)
            return

        self.group_id = self.scope['url_route']['kwargs'].get('group_id')
        self.group_channels = []

        if self.group_id:
            is_member = await database_sync_to_async(lambda: Group.objects.filter(
                id=self.group_id, members=self.user
            ).exists())()
            if not is_member:
                await self.close(code=4004)
                return
            group_channel = f"group_{self.group_id}"
            if not self._is_valid_group_name(group_channel):
                print(f"Invalid group channel name: {group_channel}")
                await self.close(code=4006)
                return
            self.group_channels = [group_channel]
        else:
            self.groups = await self.get_user_groups()
            if not self.groups:
                await self.close(code=4005)
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
        if not isinstance(name, str):
            return False
        if len(name) >= 100:
            return False
        pattern = r'^[a-zA-Z0-9\-_\.]+$'
        return bool(re.match(pattern, name))

    async def disconnect(self, close_code):
        for group_channel in self.group_channels:
            if self._is_valid_group_name(group_channel):
                await self.channel_layer.group_discard(group_channel, self.channel_name)
            else:
                print(f"Cannot discard invalid group channel name: {group_channel}")
        print(f"GroupChatConsumer disconnected for user {self.user.id} with code {close_code}")

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                message_type = data.get('type')
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

                if message_type == 'group_message':
                    message = data.get('message')
                    self.pending_metadata = data if not message else None

                    if message:
                        group_message = await database_sync_to_async(GroupMessage.objects.create)(
                            group=group,
                            sender=self.user,
                            message=message
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
                    await self.channel_layer.group_send(
                        group_channel,
                        {
                            "type": "group_message",
                            "message": message_data
                        }
                    )

                elif message_type == 'typing':
                    await self.channel_layer.group_send(
                        group_channel,
                        {
                            "type": "typing",
                            "user_id": self.user.id,
                            "first_name": self.user.first_name,
                        }
                    )

            except json.JSONDecodeError:
                await self.send(text_data=json.dumps({"type": "error", "message": "Invalid JSON data"}))
            except Exception as e:
                await self.send(text_data=json.dumps({"type": "error", "message": f"Failed to process text data: {str(e)}"}))

        if bytes_data and self.pending_metadata:
            try:
                metadata = self.pending_metadata
                file_name = metadata.get('file_name', f"unnamed_file_{datetime.now().timestamp()}")
                file_type = metadata.get('file_type', 'application/octet-stream')
                group_id = metadata.get('group_id', self.group_id)

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

                # Validate file size (e.g., max 10MB)
                max_size = 10 * 1024 * 1024  # 10MB
                if len(bytes_data) > max_size:
                    await self.send(text_data=json.dumps({
                        "type": "error",
                        "message": "File size exceeds 10MB limit"
                    }))
                    return

                # Create group message
                group_message = await database_sync_to_async(GroupMessage.objects.create)(
                    group=group,
                    sender=self.user,
                    file_name=file_name,
                    file_type=file_type
                )

                # Save file attachment
                await database_sync_to_async(group_message.attachment.save)(file_name, ContentFile(bytes_data))
                
                # Add read_by
                await database_sync_to_async(group_message.read_by.add)(self.user)

                # Serialize message
                message_data = await database_sync_to_async(
                    lambda: GroupMessageSerializer(group_message, context={'request': None}).data
                )()

                # Broadcast to group
                await self.channel_layer.group_send(
                    group_channel,
                    {
                        "type": "group_message",
                        "message": message_data
                    }
                )
                self.pending_metadata = None
            except Exception as e:
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "message": f"Failed to process file: {str(e)}"
                }))
                self.pending_metadata = None

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