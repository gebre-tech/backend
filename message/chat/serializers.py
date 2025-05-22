from rest_framework import serializers
from .models import Message

class MessageSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'message_id', 'sender', 'receiver', 'content', 'file', 'file_name',
            'file_type', 'file_size', 'created_at', 'file_url', 'nonce',
            'ephemeral_key', 'message_key', 'type'  # Added 'type'
        ]

    def create(self, validated_data):
        return Message.objects.create(**validated_data)

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        return None