from rest_framework import serializers
from .models import Group, GroupMessage
from authentication.serializers import UserSerializer

class GroupSerializer(serializers.ModelSerializer):
    admin = UserSerializer(read_only=True)
    members = UserSerializer(read_only=True, many=True)

    class Meta:
        model = Group
        fields = ['id', 'name', 'admin', 'members', 'created_at']

class GroupMessageSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    group = GroupSerializer(read_only=True)
    attachment = serializers.FileField(required=False, allow_null=True)

    class Meta:
        model = GroupMessage
        fields = ['id', 'group', 'sender', 'message', 'attachment', 'timestamp']