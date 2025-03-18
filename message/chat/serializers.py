from rest_framework import serializers
from .models import ChatRoom, ChatMessage
from authentication.serializers import UserSerializer

class ChatMessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    attachment_url = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ['id', 'sender', 'chat', 'content', 'message_type', 'attachment_url', 'timestamp', 'seen_by', 'delivered_to']
        read_only_fields = ['sender', 'timestamp']

    def get_attachment_url(self, obj):
        if obj.attachment:
            return obj.attachment.url
        return None

class ChatRoomSerializer(serializers.ModelSerializer):
    members = UserSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatRoom
        fields = ['id', 'name', 'members', 'is_group', 'created_at', 'last_message', 'unread_count']

    def get_last_message(self, obj):
        last = obj.messages.first()
        return ChatMessageSerializer(last).data if last else None

    def get_unread_count(self, obj):
        user = self.context['request'].user
        return obj.messages.exclude(seen_by=user).count()