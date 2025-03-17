# profiles/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Profile
from authentication.models import User
from .serializers import ProfileSerializer
from rest_framework.permissions import IsAuthenticated
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.utils import timezone

class CreateOrUpdateProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            profile = Profile.objects.get(user=request.user)
            serializer = ProfileSerializer(profile)
            return Response(serializer.data)
        except Profile.DoesNotExist:
            return Response({"error": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request):
        data = request.data
        try:
            profile = Profile.objects.get(user=request.user)
            profile.bio = data.get("bio", profile.bio)
            if "profile_picture" in request.FILES:
                profile.profile_picture = request.FILES["profile_picture"]
                profile.save()  # Save immediately after updating image
            profile.bio = data.get("bio", profile.bio)
            profile.save()

            user = request.user
            user.username = data.get("username", user.username)
            user.first_name = data.get("first_name", user.first_name)
            user.last_name = data.get("last_name", user.last_name)
            user.save()

            serializer = ProfileSerializer(profile)
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"profile_{request.user.id}",
                {
                    "type": "profile_update",
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "bio": profile.bio,
                    "profile_picture": profile.profile_picture.url if profile.profile_picture else None,
                    "last_seen": profile.last_seen.isoformat() if profile.last_seen else None
                }
            )
            return Response(serializer.data)
        except Profile.DoesNotExist:
            profile = Profile.objects.create(
                user=request.user,
                bio=data.get("bio", ""),
                profile_picture=request.FILES.get("profile_picture", None)
            )
            user = request.user
            user.username = data.get("username", user.username)
            user.first_name = data.get("first_name", user.first_name)
            user.last_name = data.get("last_name", user.last_name)
            user.save()
            serializer = ProfileSerializer(profile)
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"profile_{request.user.id}",
                {
                    "type": "profile_update",
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "bio": profile.bio,
                    "profile_picture": profile.profile_picture.url if profile.profile_picture else None,
                    "last_seen": profile.last_seen.isoformat() if profile.last_seen else None
                }
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)

class UpdateLastSeenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            profile = Profile.objects.get(user=request.user)
            profile.last_seen = timezone.now()
            profile.save()
            serializer = ProfileSerializer(profile)
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"profile_{request.user.id}",
                {
                    "type": "last_seen_update",
                    "last_seen": profile.last_seen.isoformat()
                }
            )
            return Response(serializer.data)
        except Profile.DoesNotExist:
            return Response({"error": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)

class FriendProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        try:
            friend = User.objects.get(username=username)
            profile = Profile.objects.get(user=friend)
            serializer = ProfileSerializer(profile)
            return Response(serializer.data)
        except (User.DoesNotExist, Profile.DoesNotExist):
            return Response({"error": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)