# profiles/serializers.py
from rest_framework import serializers
from .models import Profile
from authentication.models import User

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name']

class ProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    profile_picture = serializers.ImageField(use_url=True)  # Ensure full URL is returned

    class Meta:
        model = Profile
        fields = ['user', 'bio', 'profile_picture', 'last_seen']