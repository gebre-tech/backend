from rest_framework import serializers
from .models import Message
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

    def get_file_url(self, obj):
        if obj.file:
            # Return the Cloudinary URL directly
            return obj.file.url
        return None

    def create(self, validated_data):
        file_data = validated_data.pop('file', None)
        if file_data:
            # Upload file to Cloudinary
            upload_result = cloudinary.uploader.upload(
                file_data,
                folder='chat_files',
                resource_type='auto'  # Automatically detect file type (image, video, etc.)
            )
            validated_data['file'] = upload_result['secure_url']
            validated_data['file_name'] = upload_result['original_filename']
            validated_data['file_type'] = upload_result['resource_type'] + '/' + upload_result['format']
            validated_data['file_size'] = upload_result['bytes']
        return Message.objects.create(**validated_data)