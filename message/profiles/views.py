# Profiles views
# profiles/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Profile
from django.contrib.auth.models import User
from .serializers import ProfileSerializer
from rest_framework.permissions import IsAuthenticated

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
            profile.profile_picture = data.get("profile_picture", profile.profile_picture)
            profile.save()
            serializer = ProfileSerializer(profile)
            return Response(serializer.data)
        except Profile.DoesNotExist:
            profile = Profile.objects.create(
                user=request.user,
                bio=data.get("bio", ""),
                profile_picture=data.get("profile_picture", None)
            )
            serializer = ProfileSerializer(profile)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

class UpdateLastSeenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        profile = Profile.objects.get(user=request.user)
        profile.last_seen = request.data.get('last_seen')
        profile.save()
        return Response({"status": "Last seen updated"}, status=status.HTTP_200_OK)
    # profiles/views.py
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