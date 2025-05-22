from rest_framework import serializers
from .models import Message
from django.conf import settings
import cloudinary.uploader

class MessageSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'message_id', 'sender', 'receiver', 'content', 'file', 'file_name',
            'file_type', 'file_size', 'created_at', 'file_url', 'nonce',
            'ephemeral_key', 'message_key', 'type'
        ]

    def create(self, validated_data):
        file_data = validated_data.pop('file', None)
        message = Message.objects.create(**validated_data)
        if file_data:
            # Upload file to Cloudinary
            message.file.save(file_data.name, file_data)
            message.save()
        return message

    def get_file_url(self, obj):
        if obj.file:
            # Ensure the Cloudinary URL is fully qualified
            request = self.context.get('request')
            file_url = obj.file.url
            if not file_url.startswith('http'):
                file_url = f"{settings.SITE_URL}{file_url}"
            return file_url
        return None