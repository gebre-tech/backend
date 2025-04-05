# chat/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from .models import ChatRoom, ChatMessage
from .serializers import ChatRoomSerializer, ChatMessageSerializer
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
import logging
import os

User = get_user_model()
logger = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .models import ChatRoom, ChatMessage
from .serializers import ChatRoomSerializer, ChatMessageSerializer
from django.contrib.auth.models import User

class ChatProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, chat_id):
        try:
            chat_room = ChatRoom.objects.get(id=chat_id)
            if not chat_room.members.filter(id=request.user.id).exists():
                return Response({"error": "You are not a member of this chat"}, status=status.HTTP_403_FORBIDDEN)

            serializer = ChatRoomSerializer(chat_room, context={'request': request})
            data = serializer.data

            # Add additional fields for one-on-one chats
            if not chat_room.is_group:
                other_member = chat_room.members.exclude(id=request.user.id).first()
                if other_member:
                    data["user"] = {
                        "id": str(other_member.id),
                        "first_name": other_member.first_name,
                        "username": other_member.username,
                        "profile_picture": other_member.profile_picture.url if other_member.profile_picture else None,
                    }
                    data["is_online"] = is_user_online(other_member.last_seen)  # Implement this function
                    data["last_seen"] = other_member.last_seen.isoformat() if other_member.last_seen else None
                    data["profile_picture"] = other_member.profile_picture.url if other_member.profile_picture else None

            # Add pinned message
            pinned_message = chat_room.messages.filter(isPinned=True).first()
            if pinned_message:
                data["pinned_message"] = ChatMessageSerializer(pinned_message).data

            return Response(data, status=status.HTTP_200_OK)
        except ChatRoom.DoesNotExist:
            return Response({"error": "Chat not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# Helper function to determine if a user is online
def is_user_online(last_seen):
    from datetime import datetime, timedelta
    if not last_seen:
        return False
    now = datetime.utcnow().replace(tzinfo=last_seen.tzinfo)
    return (now - last_seen) < timedelta(minutes=5)

class SendMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logger.info("Entering SendMessageView.post")
        receiver_id = request.data.get("receiver_id")
        content = request.data.get("content", "")
        message_type = request.data.get("message_type", "text")
        attachment_url = request.data.get("attachment_url")
        forward_id = request.data.get("forward_id")

        if not receiver_id and message_type != "system":
            logger.warning("Receiver ID required for non-system message")
            return Response({"error": "Receiver ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if message_type != "system" and not content.strip() and not attachment_url:
            logger.warning("Content or attachment required for non-system message")
            return Response({"error": "Content or attachment is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            chat_room = None
            if receiver_id:
                receiver = User.objects.get(id=receiver_id)
                chat_room = ChatRoom.objects.filter(
                    is_group=False, members=request.user
                ).filter(members=receiver).first()
                if not chat_room:
                    chat_room = ChatRoom.objects.create(name="", is_group=False)
                    chat_room.members.add(request.user, receiver)
                    chat_room.save()
                    logger.info(f"Created new chat room {chat_room.id} for {request.user.username} and {receiver.username}")

            forwarded_from = None
            if forward_id:
                forwarded_from = ChatMessage.objects.get(id=forward_id)

            message = ChatMessage(
                sender=request.user,
                chat=chat_room,
                content=content,
                message_type=message_type,
                attachment_url=attachment_url,
                forwarded_from=forwarded_from
            )
            message.save()
            message.delivered_to.add(*chat_room.members.all())
            logger.info(f"Message {message.id} saved and delivered to {chat_room.members.count()} members")

            serializer = ChatMessageSerializer(message, context={'request': request})
            response_data = serializer.data
            response_data["chat"] = {"id": str(chat_room.id)}
            return Response(response_data, status=status.HTTP_201_CREATED)

        except User.DoesNotExist:
            logger.error(f"Receiver with ID {receiver_id} not found")
            return Response({"error": "Receiver not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Unexpected error in SendMessageView: {str(e)}", exc_info=True)
            return Response({"error": f"Server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class GetMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, chat_id):
        try:
            limit = int(request.query_params.get("limit", 100))  # Default limit is 100
            chat_room = ChatRoom.objects.get(id=chat_id, members=request.user)
            messages = ChatMessage.objects.filter(chat_id=chat_id).order_by("timestamp")[:limit]
            serializer = ChatMessageSerializer(messages, many=True, context={'request': request})
            logger.info(f"Retrieved {len(messages)} messages for chat {chat_id}")
            return Response(serializer.data)
        except ChatRoom.DoesNotExist:
            logger.error(f"Chat room {chat_id} not found for user {request.user.username}")
            return Response({"error": "Chat not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in GetMessagesView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MarkAsReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, message_id):
        try:
            message = ChatMessage.objects.get(id=message_id, chat__members=request.user)
            message.seen_by.add(request.user)
            logger.info(f"Message {message_id} marked as read by {request.user.username}")
            serializer = ChatMessageSerializer(message, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except ChatMessage.DoesNotExist:
            logger.error(f"Message {message_id} not found")
            return Response({"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in MarkAsReadView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MarkAsReadBatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        message_ids = request.data.get("message_ids", [])
        if not message_ids:
            return Response({"error": "No message IDs provided"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            messages = ChatMessage.objects.filter(id__in=message_ids, chat__members=request.user)
            if not messages.exists():
                return Response({"error": "No valid messages found"}, status=status.HTTP_404_NOT_FOUND)
            for message in messages:
                message.seen_by.add(request.user)
            logger.info(f"Messages {message_ids} marked as read by {request.user.username}")
            return Response({"status": "Messages marked as read"}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error in MarkAsReadBatchView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ReactToMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, message_id):
        emoji = request.data.get("emoji")
        if not emoji:
            return Response({"error": "Emoji is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            message = ChatMessage.objects.get(id=message_id, chat__members=request.user)
            message.reactions.append(emoji)
            message.save(update_fields=["reactions"])
            logger.info(f"Reaction {emoji} added to message {message_id} by {request.user.username}")
            serializer = ChatMessageSerializer(message, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except ChatMessage.DoesNotExist:
            logger.error(f"Message {message_id} not found")
            return Response({"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in ReactToMessageView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ChatRoomListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            chat_rooms = ChatRoom.objects.filter(members=request.user).order_by("-updated_at")
            serializer = ChatRoomSerializer(chat_rooms, many=True, context={'request': request})
            logger.info(f"Retrieved {chat_rooms.count()} chat rooms for {request.user.username}")
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error in ChatRoomListView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class UploadAttachmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, chat_id):
        file = request.FILES.get("file")
        if not file:
            logger.error("No file uploaded in UploadAttachmentView")
            return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            chat_room = ChatRoom.objects.get(id=chat_id, members=request.user)
            fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, "uploads"))
            filename = fs.save(file.name, file)
            file_url = request.build_absolute_uri(fs.url(filename))
            logger.info(f"Uploaded attachment {filename} for chat {chat_id}")
            # Create a message with the attachment
            message_type = 'image' if file.name.split('.')[-1].lower() in ['jpg', 'jpeg', 'png'] else 'file'
            message = ChatMessage(
                sender=request.user,
                chat=chat_room,
                message_type=message_type,
                attachment_url=file_url
            )
            message.save()
            message.delivered_to.add(*chat_room.members.all())
            serializer = ChatMessageSerializer(message, context={'request': request})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ChatRoom.DoesNotExist:
            logger.error(f"Chat room {chat_id} not found for upload_attachment")
            return Response({"error": "Chat not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in UploadAttachmentView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_group_chat(request):
    name = request.data.get("name")
    member_ids = request.data.get("members", [])
    if not name or not member_ids:
        logger.error("Missing name or members in create_group_chat")
        return Response({"error": "Group name and at least one member required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        members = User.objects.filter(id__in=member_ids)
        if not members.exists():
            logger.error("No valid members found for group chat")
            return Response({"error": "No valid members found"}, status=status.HTTP_400_BAD_REQUEST)
        chat_room = ChatRoom.objects.create(name=name, is_group=True)
        chat_room.members.set(members)
        chat_room.members.add(request.user)
        chat_room.admins.add(request.user)
        message = ChatMessage.objects.create(
            sender=request.user,
            chat=chat_room,
            content=f"Group '{name}' created.",
            message_type="system"
        )
        message.delivered_to.add(*chat_room.members.all())
        serializer = ChatRoomSerializer(chat_room, context={'request': request})
        logger.info(f"Created group chat {chat_room.id} with name {name}")
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    except Exception as e:
        logger.error(f"Error in create_group_chat: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def edit_message(request, message_id):
    content = request.data.get("content")
    if not content:
        return Response({"error": "Content is required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        message = ChatMessage.objects.get(id=message_id, sender=request.user)
        message.edit(content)
        serializer = ChatMessageSerializer(message, context={'request': request})
        logger.info(f"Message {message_id} edited by {request.user.username}")
        return Response(serializer.data, status=status.HTTP_200_OK)
    except ChatMessage.DoesNotExist:
        logger.error(f"Message {message_id} not found or not owned by {request.user.username}")
        return Response({"error": "Message not found or unauthorized"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in edit_message: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_message(request, message_id):
    try:
        message = ChatMessage.objects.get(id=message_id, sender=request.user)
        message.delete()
        logger.info(f"Message {message_id} deleted by {request.user.username}")
        return Response({"status": "Message deleted"}, status=status.HTTP_204_NO_CONTENT)
    except ChatMessage.DoesNotExist:
        logger.error(f"Message {message_id} not found or not owned by {request.user.username}")
        return Response({"error": "Message not found or unauthorized"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in delete_message: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def pin_message(request, chat_id, message_id):
    try:
        chat_room = ChatRoom.objects.get(id=chat_id, admins=request.user)
        message = ChatMessage.objects.get(id=message_id, chat=chat_room)
        chat_room.pinned_message = message
        chat_room.save()
        logger.info(f"Message {message_id} pinned in chat {chat_id} by {request.user.username}")
        return Response({"status": "Message pinned"}, status=status.HTTP_200_OK)
    except ChatRoom.DoesNotExist:
        logger.error(f"Chat room {chat_id} not found or user {request.user.username} not admin")
        return Response({"error": "Chat not found or unauthorized"}, status=status.HTTP_404_NOT_FOUND)
    except ChatMessage.DoesNotExist:
        logger.error(f"Message {message_id} not found in chat {chat_id}")
        return Response({"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in pin_message: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def manage_group_member(request, chat_id):
    action = request.data.get("action")
    member_id = request.data.get("member_id")
    if not action or not member_id:
        return Response({"error": "Action and member ID required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        chat_room = ChatRoom.objects.get(id=chat_id, admins=request.user)
        member = User.objects.get(id=member_id)
        if action == "add":
            chat_room.add_member(member, request.user)
        elif action == "remove":
            chat_room.remove_member(member, request.user)
        elif action == "promote":
            chat_room.admins.add(member)
            ChatMessage.objects.create(
                chat=chat_room,
                sender=request.user,
                content=f"{member.username} was promoted to admin.",
                message_type="system"
            )
        elif action == "demote":
            chat_room.admins.remove(member)
            ChatMessage.objects.create(
                chat=chat_room,
                sender=request.user,
                content=f"{member.username} was demoted from admin.",
                message_type="system"
            )
        else:
            return Response({"error": "Invalid action"}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ChatRoomSerializer(chat_room, context={'request': request})
        logger.info(f"Group action {action} performed on {member.username} in chat {chat_id}")
        return Response(serializer.data, status=status.HTTP_200_OK)
    except ChatRoom.DoesNotExist:
        logger.error(f"Chat room {chat_id} not found or user {request.user.username} not admin")
        return Response({"error": "Chat not found or unauthorized"}, status=status.HTTP_404_NOT_FOUND)
    except User.DoesNotExist:
        logger.error(f"Member {member_id} not found")
        return Response({"error": "Member not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in manage_group_member: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)