from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from .models import ChatRoom, ChatMessage
from .serializers import ChatRoomSerializer, ChatMessageSerializer
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from rest_framework.decorators import api_view, permission_classes
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

class SendMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        receiver_id = request.data.get("receiver_id")
        content = request.data.get("content", "")  # Default to empty string if not provided
        message_type = request.data.get("message_type", "text")
        attachment = request.FILES.get("attachment")

        if not receiver_id:
            return Response({"error": "Receiver ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            receiver = User.objects.get(id=receiver_id)
            # Find or create a one-on-one chat room
            chat_room = ChatRoom.objects.filter(is_group=False, members=request.user).filter(members=receiver).first()
            if not chat_room:
                chat_room = ChatRoom.objects.create(name="", is_group=False)
                chat_room.members.add(request.user, receiver)
                logger.info(f"Created new chat room {chat_room.id} between {request.user.username} and {receiver.username}")
            else:
                logger.info(f"Using existing chat room {chat_room.id}")

            # Create the message
            message = ChatMessage(
                sender=request.user,
                chat=chat_room,
                content=content,
                message_type=message_type,
            )
            if attachment:
                file_name = default_storage.save(f"chat_attachments/{attachment.name}", ContentFile(attachment.read()))
                message.attachment = file_name
                logger.info(f"Attachment saved: {file_name}")

            message.save()
            message.delivered_to.add(*chat_room.members.all())  # Mark as delivered to all members
            serializer = ChatMessageSerializer(message, context={'request': request})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except User.DoesNotExist:
            logger.error(f"Receiver with ID {receiver_id} not found")
            return Response({"error": "Receiver not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in SendMessageView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class GetMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, chat_id):  # Updated to use chat_id instead of user_id
        try:
            chat_room = ChatRoom.objects.get(id=chat_id, members=request.user)
            messages = chat_room.messages.all().order_by('timestamp')
            serializer = ChatMessageSerializer(messages, many=True, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
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
            return Response({"status": "Message marked as read"}, status=status.HTTP_200_OK)
        except ChatMessage.DoesNotExist:
            logger.error(f"Message {message_id} not found")
            return Response({"error": "Message not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Error in MarkAsReadView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ChatRoomListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            chat_rooms = ChatRoom.objects.filter(members=request.user).order_by('-updated_at')
            serializer = ChatRoomSerializer(chat_rooms, many=True, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error in ChatRoomListView: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_attachment(request, chat_id):
    file = request.FILES.get("file")
    if not file:
        logger.error("No file uploaded in upload_attachment")
        return Response({"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        chat_room = ChatRoom.objects.get(id=chat_id, members=request.user)
        file_name = default_storage.save(f"chat_attachments/{file.name}", ContentFile(file.read()))
        file_url = default_storage.url(file_name)
        logger.info(f"Uploaded attachment {file_name} for chat {chat_id}")
        return Response({"file_url": file_url}, status=status.HTTP_201_CREATED)
    except ChatRoom.DoesNotExist:
        logger.error(f"Chat room {chat_id} not found for upload_attachment")
        return Response({"error": "Chat not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in upload_attachment: {str(e)}")
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
        logger.info(f"Created group chat {chat_room.id} with name {name}")

        # Create a system message for group creation
        message = ChatMessage.objects.create(
            sender=request.user,
            chat=chat_room,
            content=f"Group '{name}' created.",
            message_type="text"
        )
        message.delivered_to.add(*chat_room.members.all())

        serializer = ChatRoomSerializer(chat_room, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    except Exception as e:
        logger.error(f"Error in create_group_chat: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)