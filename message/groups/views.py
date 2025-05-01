from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Group, GroupMessage
from .serializers import GroupSerializer, GroupMessageSerializer
from authentication.models import User
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator, EmptyPage
from django.utils.dateparse import parse_datetime

class GroupDetailsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return Response(
                    {"error": "You are not a member of this group"},
                    status=status.HTTP_403_FORBIDDEN
                )

            total_messages = GroupMessage.objects.filter(group=group).count()
            total_members = group.members.count()

            group_data = {
                "id": group.id,
                "name": group.name,
                "created_at": group.created_at,
                "total_members": total_members,
                "total_messages": total_messages,
                "admin": {
                    "id": group.admin.id,
                    "first_name": group.admin.first_name,
                    "username": group.admin.username,
                },
                "profile_picture": group.profile_picture.url if group.profile_picture else None,
            }

            return Response(group_data, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class UpdateGroupProfilePictureView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if group.admin != request.user:
                return Response(
                    {"error": "Only admins can update the group profile picture"},
                    status=status.HTTP_403_FORBIDDEN
                )

            print("Request FILES:", request.FILES)
            print("Request POST:", request.POST)

            profile_picture = request.FILES.get("profile_picture", None)
            if not profile_picture:
                return Response(
                    {"error": "No profile picture provided"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            group.profile_picture = profile_picture
            group.save()

            serializer = GroupSerializer(group)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class ListGroupsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get('query', None)
        groups = Group.objects.filter(members=request.user)
        
        if query:
            groups = groups.filter(name__icontains=query)
        
        serializer = GroupSerializer(groups, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class CreateGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        group_name = request.data.get("name")
        members_ids = request.data.get("members", [])

        admin = request.user
        members = User.objects.filter(id__in=members_ids)

        group = Group.objects.create(name=group_name, admin=admin)
        group.members.set(members)
        group.members.add(admin)
        group.save()

        serializer = GroupSerializer(group)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class SendGroupMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        group_id = request.data.get("group_id")
        message = request.data.get("message")
        attachment = request.FILES.get("attachment", None)

        try:
            group = Group.objects.get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return Response(
                    {"error": "You are not a member of this group"},
                    status=status.HTTP_403_FORBIDDEN
                )

            group_message = GroupMessage.objects.create(
                group=group,
                sender=request.user,
                message=message,
                attachment=attachment
            )

            serializer = GroupMessageSerializer(group_message)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class GetGroupMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return Response(
                    {"error": "You are not a member of this group"},
                    status=status.HTTP_403_FORBIDDEN
                )

            messages = GroupMessage.objects.filter(group=group).order_by('timestamp')
            since = request.query_params.get('since')
            if since:
                since_dt = parse_datetime(since)
                if since_dt:
                    messages = messages.filter(timestamp__gt=since_dt)

            page = request.query_params.get('page', 1)
            page_size = request.query_params.get('page_size', 20)

            paginator = Paginator(messages, page_size)
            try:
                paginated_messages = paginator.page(page)
                serializer = GroupMessageSerializer(paginated_messages, many=True)
                return Response({
                    'results': serializer.data,
                    'next': paginated_messages.has_next(),
                    'previous': paginated_messages.has_previous(),
                    'count': paginator.count
                })
            except EmptyPage:
                return Response({
                    'results': [],
                    'next': False,
                    'previous': paginator.num_pages > 1,
                    'count': paginator.count
                }, status=status.HTTP_200_OK)

        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class AddMemberToGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id, user_id):
        try:
            group = Group.objects.get(id=group_id)
            user = User.objects.get(id=user_id)

            if group.admin != request.user:
                return Response({"error": "Only admins can add members"}, status=status.HTTP_403_FORBIDDEN)

            group.members.add(user)
            group.save()

            return Response({"status": "User added to group"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class RemoveMemberFromGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id, user_id):
        try:
            group = Group.objects.get(id=group_id)
            user = User.objects.get(id=user_id)

            if group.admin != request.user:
                return Response({"error": "Only admins can remove members"}, status=status.HTTP_403_FORBIDDEN)

            group.members.remove(user)
            group.save()

            return Response({"status": "User removed from group"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)