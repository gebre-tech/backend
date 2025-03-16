# contacts/serializers.py
from rest_framework import serializers
from .models import Contact, FriendRequest
from authentication.serializers import UserSerializer

class FriendRequestSerializer(serializers.ModelSerializer):
    sender = UserSerializer(read_only=True)
    receiver = UserSerializer(read_only=True)

    class Meta:
        model = FriendRequest
        fields = ['id', 'sender', 'receiver', 'created_at', 'accepted']

class ContactSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    friend = UserSerializer(read_only=True)
    friend_id = serializers.IntegerField(source='friend.id', read_only=True)

    class Meta:
        model = Contact
        fields = ['id', 'user', 'friend', 'friend_id', 'created_at']
        read_only_fields = ['user', 'created_at']