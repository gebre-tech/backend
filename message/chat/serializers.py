# chat/serializers.py
from rest_framework import serializers
from .models import ChatRoom, ChatMessage, MessageSeen
from django.conf import settings
from authentication.serializers import UserSerializer
import mimetypes    

class MessageSeenSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    seen_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = MessageSeen
        fields = ['user', 'seen_at']


class ChatMessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    seen_by_details = serializers.SerializerMethodField()
    delivered_to = UserSerializer(many=True, read_only=True)
    forwarded_from = serializers.SerializerMethodField()
    chat = serializers.PrimaryKeyRelatedField(queryset=ChatRoom.objects.all())
    attachment = serializers.FileField(required=False, allow_null=True, write_only=True)
    attachment_url = serializers.SerializerMethodField()
    attachment_mime_type = serializers.CharField(read_only=True)
    attachment_size = serializers.IntegerField(read_only=True)
    attachment_name = serializers.CharField(read_only=True)

    class Meta:
        model = ChatMessage
        fields = [
            'id', 'sender', 'chat', 'content', 'message_type', 'attachment', 'attachment_url',
            'attachment_mime_type', 'attachment_size', 'attachment_name', 'timestamp',
            'edited_at', 'is_deleted', 'forwarded_from', 'seen_by_details', 'delivered_to',
            'reactions'
        ]
        read_only_fields = [
            'sender', 'timestamp', 'edited_at', 'is_deleted', 'seen_by_details',
            'delivered_to', 'attachment_url', 'attachment_mime_type', 'attachment_size',
            'attachment_name'
        ]

    def get_attachment_url(self, obj):
        # Use obj.attachment.url directly instead of obj.attachment_url
        if obj.attachment and hasattr(obj.attachment, 'url'):
            base_url = settings.SITE_URL.rstrip('/')  # e.g., 'http://127.0.0.1:8000'
            relative_url = obj.attachment.url  # e.g., '/media/chat_attachments/2025/04/16/filename.jpg'
            return f"{base_url}{relative_url}"
        return None

    def get_seen_by_details(self, obj):
        seen_by = obj.messageseen_set.all()
        return MessageSeenSerializer(seen_by, many=True, read_only=True).data

    def get_forwarded_from(self, obj):
        if obj.forwarded_from:
            return ChatMessageSerializer(obj.forwarded_from, context=self.context).data
        return None

    def validate_attachment(self, value):
        if value:
            max_size = 100 * 1024 * 1024  # 100MB
            if value.size > max_size:
                raise serializers.ValidationError(f"File size exceeds {max_size / (1024 * 1024)}MB limit")
            allowed_mime_types = [
                'image/', 'video/', 'audio/', 'application/pdf',
                'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'text/plain'
            ]
            mime_type, _ = mimetypes.guess_type(value.name)
            if mime_type and not any(mime_type.startswith(allowed) for allowed in allowed_mime_types):
                raise serializers.ValidationError(f"Unsupported file type: {mime_type}")
        return value

    def create(self, validated_data):
        attachment = validated_data.pop('attachment', None)
        instance = super().create(validated_data)
        if attachment:
            instance.attachment = attachment
            instance.save()
        return instance

    def update(self, instance, validated_data):
        attachment = validated_data.pop('attachment', None)
        instance = super().update(instance, validated_data)
        if attachment:
            instance.attachment = attachment
            instance.save()
        return instance

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.forwarded_from:
            representation['forwarded_from'] = self.get_forwarded_from(instance)
        return representation

class ChatMessageMiniSerializer(serializers.ModelSerializer):
    attachment_url = serializers.SerializerMethodField()
    attachment_name = serializers.CharField(read_only=True)

    class Meta:
        model = ChatMessage
        fields = ['id', 'content', 'message_type', 'timestamp', 'attachment_url', 'attachment_name']

    def get_attachment_url(self, obj):
        if obj.attachment and hasattr(obj.attachment, 'url'):
            base_url = settings.SITE_URL.rstrip('/')
            relative_url = obj.attachment.url
            return f"{base_url}{relative_url}"
        return None

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