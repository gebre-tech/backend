from rest_framework import serializers
from .models import Profile
from authentication.models import User
import cloudinary
from django.conf import settings

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name']

class ProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    profile_picture = serializers.SerializerMethodField()

    def get_profile_picture(self, obj):
        if obj.profile_picture:
            # If it's already a URL (from Cloudinary), return it directly
            if obj.profile_picture.startswith('http'):
                return obj.profile_picture
            
            # If it's a Cloudinary public_id, construct the URL
            if hasattr(settings, 'CLOUDINARY_URL'):
                return cloudinary.CloudinaryImage(obj.profile_picture).build_url()
            
            # Fallback to local media URL
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.profile_picture.url)
        return None

    class Meta:
        model = Profile
        fields = ['user', 'bio', 'profile_picture', 'last_seen']