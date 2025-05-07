from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Group, GroupMessage
from .serializers import GroupSerializer, GroupMessageSerializer
from authentication.models import User
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator, EmptyPage
from django.utils.dateparse import parse_datetime
import logging

logger = logging.getLogger(__name__)

class GetGroupMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, group_id):
        logger.info(f"Fetching messages for group {group_id} by user {request.user.id}")
        try:
            group = Group.objects.get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                logger.warning(f"User {request.user.id} is not a member of group {group_id}")
                return Response({"error": "You are not a member of this group"}, status=status.HTTP_403_FORBIDDEN)

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
                serializer = GroupMessageSerializer(paginated_messages, many=True, context={'request': request})
                logger.info(f"Returning {len(serializer.data)} messages for group {group_id}")
                return Response({
                    'results': serializer.data,
                    'next': paginated_messages.has_next(),
                    'previous': paginated_messages.has_previous(),
                    'count': paginator.count
                })
            except EmptyPage:
                logger.info(f"Empty page for group {group_id}, page {page}")
                return Response({
                    'results': [],
                    'next': False,
                    'previous': paginator.num_pages > 1,
                    'count': paginator.count
                }, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            logger.error(f"Group {group_id} not found")
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error fetching messages for group {group_id}: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
                "creator": {
                    "id": group.creator.id,
                    "first_name": group.creator.first_name,
                    "username": group.creator.username,
                },
                "admins": [
                    {
                        "id": admin.id,
                        "first_name": admin.first_name,
                        "username": admin.username,
                    }
                    for admin in group.admins.all()
                ],
                "profile_picture": group.profile_picture.url if group.profile_picture else None,
                "can_members_send_messages": group.can_members_send_messages,
            }

            return Response(group_data, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class UpdateGroupProfilePictureView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if request.user not in group.admins.all():
                return Response(
                    {"error": "Only admins can update the group profile picture"},
                    status=status.HTTP_403_FORBIDDEN
                )

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

        creator = request.user
        members = User.objects.filter(id__in=members_ids)

        group = Group.objects.create(name=group_name, creator=creator)
        group.members.set(members)
        group.members.add(creator)
        group.admins.add(creator)
        group.save()

        serializer = GroupSerializer(group)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class SendGroupMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        group_id = request.data.get("group_id")
        message = request.data.get("message")
        attachment = request.FILES.get("attachment", None)
        file_name = request.data.get("file_name", attachment.name if attachment else None)
        file_type = request.data.get("file_type", attachment.content_type if attachment else None)

        try:
            group = Group.objects.get(id=group_id)
            if not group.members.filter(id=request.user.id).exists():
                return Response(
                    {"error": "You are not a member of this group"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Check if non-admin members can send messages
            if not group.can_members_send_messages and request.user not in group.admins.all():
                return Response(
                    {"error": "You are not allowed to send messages in this group"},
                    status=status.HTTP_403_FORBIDDEN
                )

            if attachment:
                max_size = 10 * 1024 * 1024  # 10MB
                if attachment.size > max_size:
                    return Response(
                        {"error": "File size exceeds 10MB limit"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            group_message = GroupMessage.objects.create(
                group=group,
                sender=request.user,
                message=message,
                attachment=attachment,
                file_name=file_name,
                file_type=file_type
            )

            serializer = GroupMessageSerializer(group_message, context={'request': request})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error sending message for group {group_id}: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class AddMemberToGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id, user_id):
        try:
            group = Group.objects.get(id=group_id)
            user = User.objects.get(id=user_id)

            if request.user not in group.admins.all():
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

            if request.user not in group.admins.all():
                return Response({"error": "Only admins can remove members"}, status=status.HTTP_403_FORBIDDEN)

            group.members.remove(user)
            group.save()

            return Response({"status": "User removed from group"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class AssignAdminView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id, user_id):
        try:
            group = Group.objects.get(id=group_id)
            user = User.objects.get(id=user_id)

            if request.user != group.creator:
                return Response(
                    {"error": "Only the group owner can assign admin rights"},
                    status=status.HTTP_403_FORBIDDEN
                )

            if user not in group.members.all():
                return Response(
                    {"error": "User must be a member of the group to be assigned as admin"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if user in group.admins.all():
                return Response(
                    {"error": "User is already an admin"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            group.admins.add(user)
            group.save()

            return Response({"status": "User assigned as admin"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class RevokeAdminView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id, user_id):
        try:
            group = Group.objects.get(id=group_id)
            user = User.objects.get(id=user_id)

            if request.user != group.creator:
                return Response(
                    {"error": "Only the group owner can revoke admin rights"},
                    status=status.HTTP_403_FORBIDDEN
                )

            if user == group.creator:
                return Response(
                    {"error": "Cannot revoke admin rights from the group owner"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if user not in group.admins.all():
                return Response(
                    {"error": "User is not an admin"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            group.admins.remove(user)
            group.save()

            return Response({"status": "Admin rights revoked"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class LeaveGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if request.user == group.creator:
                return Response(
                    {"error": "The group owner cannot leave the group"},
                    status=status.HTTP_403_FORBIDDEN
                )
            if not group.members.filter(id=request.user.id).exists():
                return Response(
                    {"error": "You are not a member of this group"},
                    status=status.HTTP_403_FORBIDDEN
                )

            group.members.remove(request.user)
            group.save()

            return Response({"status": "Successfully left the group"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class DeleteGroupView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if request.user != group.creator:
                return Response(
                    {"error": "Only the group owner can delete the group"},
                    status=status.HTTP_403_FORBIDDEN
                )
            group.delete()
            return Response({"status": "Group deleted successfully"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)

class EditGroupMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, group_id, message_id):
        try:
            group = Group.objects.get(id=group_id)
            message = GroupMessage.objects.get(id=message_id, group=group)
            
            # Only sender, admins, or creator can edit
            is_admin_or_creator = request.user in group.admins.all() or request.user == group.creator
            if message.sender != request.user and not is_admin_or_creator:
                return Response(
                    {"error": "You are not authorized to edit this message"},
                    status=status.HTTP_403_FORBIDDEN
                )

            new_message = request.data.get("message")
            if not new_message:
                return Response(
                    {"error": "New message content is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            message.message = new_message
            message.save()

            serializer = GroupMessageSerializer(message, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except GroupMessage.DoesNotExist:
            return Response({"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND)

class DeleteGroupMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, group_id, message_id):
        try:
            group = Group.objects.get(id=group_id)
            message = GroupMessage.objects.get(id=message_id, group=group)
            
            # Only sender, admins, or creator can delete
            is_admin_or_creator = request.user in group.admins.all() or request.user == group.creator
            if message.sender != request.user and not is_admin_or_creator:
                return Response(
                    {"error": "You are not authorized to delete this message"},
                    status=status.HTTP_403_FORBIDDEN
                )

            message.delete()
            return Response({"status": "Message deleted successfully"}, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)
        except GroupMessage.DoesNotExist:
            return Response({"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND)

class ToggleMemberMessagingView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        try:
            group = Group.objects.get(id=group_id)
            if request.user not in group.admins.all():
                return Response(
                    {"error": "Only admins can toggle messaging permissions"},
                    status=status.HTTP_403_FORBIDDEN
                )

            group.can_members_send_messages = not group.can_members_send_messages
            group.save()

            return Response({
                "status": "Messaging permissions updated",
                "can_members_send_messages": group.can_members_send_messages
            }, status=status.HTTP_200_OK)
        except Group.DoesNotExist:
            return Response({"error": "Group not found"}, status=status.HTTP_404_NOT_FOUND)