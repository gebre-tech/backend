# chat/serializers.py
from rest_framework import serializers
from .models import ChatRoom, ChatMessage, MessageSeen
from authentication.serializers import UserSerializer

class MessageSeenSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    seen_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = MessageSeen
        fields = ['user', 'seen_at']

class ChatMessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    attachment_url = serializers.SerializerMethodField()  # For frontend compatibility
    seen_by_details = MessageSeenSerializer(source='messageseen_set', many=True, read_only=True)
    delivered_to = UserSerializer(many=True, read_only=True)
    forwarded_from = 'self'  # Recursive reference
    chat = serializers.PrimaryKeyRelatedField(queryset=ChatRoom.objects.all())  # Required for creation

    class Meta:
        model = ChatMessage
        fields = [
            'id', 'sender', 'chat', 'content', 'message_type', 'attachment', 'attachment_url',
            'timestamp', 'edited_at', 'is_deleted', 'forwarded_from', 'seen_by_details', 'delivered_to'
        ]
        read_only_fields = ['sender', 'timestamp', 'edited_at', 'is_deleted', 'seen_by_details', 'delivered_to']

    def get_attachment_url(self, obj):
        if obj.attachment:
            return self.context['request'].build_absolute_uri(obj.attachment.url)
        return None

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.forwarded_from:
            representation['forwarded_from'] = ChatMessageSerializer(instance.forwarded_from, context=self.context).data
        return representation

class ChatRoomSerializer(serializers.ModelSerializer):
    members = UserSerializer(many=True, read_only=True)
    admins = UserSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    pinned_message = ChatMessageSerializer(read_only=True)

    class Meta:
        model = ChatRoom
        fields = [
            'id', 'name', 'members', 'admins', 'is_group', 'created_at',
            'updated_at', 'last_message', 'unread_count', 'pinned_message'
        ]

    def get_last_message(self, obj):
        last = obj.messages.filter(is_deleted=False).order_by('-timestamp').first()
        return ChatMessageSerializer(last, context=self.context).data if last else None

    def get_unread_count(self, obj):
        user = self.context.get('request').user if self.context.get('request') else None
        if user and not user.is_anonymous:
            return obj.messages.exclude(seen_by=user).count()
        return 0