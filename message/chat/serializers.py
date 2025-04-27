from rest_framework import serializers
from .models import Message

class MessageSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()  # For media URL

    class Meta:
        model = Message
        fields = ['id', 'sender', 'receiver', 'content', 'file', 'file_name', 'file_type', 'created_at', 'file_url', 'nonce']  # Added 'nonce'

    def create(self, validated_data):
        """
        Explicitly define how to create a Message instance.
        """
        return Message.objects.create(**validated_data)

    def get_file_url(self, obj):
        """Generate the full URL for the file."""
        if obj.file:
            request = self.context.get('request')
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        return None