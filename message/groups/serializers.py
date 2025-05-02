from rest_framework import serializers
from .models import Group, GroupMessage
from authentication.serializers import UserSerializer

class GroupSerializer(serializers.ModelSerializer):
    creator = UserSerializer(read_only=True)
    admins = UserSerializer(read_only=True, many=True)
    members = UserSerializer(read_only=True, many=True)
    profile_picture = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Group
        fields = ['id', 'name', 'creator', 'admins', 'members', 'created_at', 'profile_picture']

class GroupMessageSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()
    attachment = serializers.FileField(required=False, allow_null=True)
    reactions = serializers.JSONField(default=dict)
    read_by = UserSerializer(many=True, read_only=True)

    def get_sender(self, obj):
        return {
            "id": obj.sender.id,
            "first_name": obj.sender.first_name,
            "username": obj.sender.username
        }

    def get_group(self, obj):
        return {"id": obj.group.id, "name": obj.group.name}

    class Meta:
        model = GroupMessage
        fields = ['id', 'group', 'sender', 'message', 'attachment', 'timestamp', 'reactions', 'read_by']