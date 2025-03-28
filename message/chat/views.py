# chat/views.py
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
from django.views.decorators.csrf import csrf_exempt

User = get_user_model()
logger = logging.getLogger(__name__)

# chat/views.py (SendMessageView only)
class SendMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logger.info("Entering SendMessageView.post")
        receiver_id = request.data.get("receiver_id")
        content = request.data.get("content", "")
        message_type = request.data.get("message_type", "text")
        attachment_url = request.data.get("attachment_url")
        forward_id = request.data.get("forward_id")

        logger.info(f"Request data: receiver_id={receiver_id}, content='{content}', message_type={message_type}, attachment_url={attachment_url}, forward_id={forward_id}")
        logger.info(f"Authenticated user: id={request.user.id}, username={request.user.username}")

        if not receiver_id and message_type != "system":
            logger.warning("Receiver ID required for non-system message")
            return Response({"error": "Receiver ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            chat_room = None
            if receiver_id:
                logger.info(f"Fetching receiver with ID {receiver_id}")
                try:
                    receiver_id = int(receiver_id)
                    receiver = User.objects.get(id=receiver_id)
                    logger.info(f"Receiver found: id={receiver.id}, username={receiver.username}")
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid receiver_id: {receiver_id}, error: {str(e)}")
                    return Response({"error": "Invalid receiver ID"}, status=status.HTTP_400_BAD_REQUEST)
                except User.DoesNotExist:
                    logger.error(f"Receiver with ID {receiver_id} not found")
                    return Response({"error": "Receiver not found"}, status=status.HTTP_404_NOT_FOUND)

                logger.info("Checking for existing chat room")
                try:
                    chat_room = ChatRoom.objects.filter(
                        is_group=False, members=request.user
                    ).filter(members=receiver).first()
                    if not chat_room:
                        logger.info("No existing chat room found, creating new one")
                        chat_room = ChatRoom.objects.create(name="", is_group=False)
                        logger.info(f"New chat room created: id={chat_room.id}")
                        chat_room.members.add(request.user, receiver)
                        chat_room.save()
                        logger.info(f"Members added to chat room {chat_room.id}: {request.user.username}, {receiver.username}")
                except Exception as e:
                    logger.error(f"Failed to fetch or create chat room: {str(e)}", exc_info=True)
                    return Response({"error": f"Chat room error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                logger.error("System message requires a chat room")
                return Response({"error": "System message requires a chat room"}, status=status.HTTP_400_BAD_REQUEST)

            forwarded_from = None
            if forward_id:
                logger.info(f"Fetching forwarded message with ID {forward_id}")
                try:
                    forward_id = int(forward_id)
                    forwarded_from = ChatMessage.objects.get(id=forward_id)
                    logger.info(f"Forwarded message found: id={forwarded_from.id}")
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid forward_id: {forward_id}, error: {str(e)}")
                    return Response({"error": "Invalid forward ID"}, status=status.HTTP_400_BAD_REQUEST)
                except ChatMessage.DoesNotExist:
                    logger.error(f"Forwarded message {forward_id} not found")
                    return Response({"error": "Forwarded message not found"}, status=status.HTTP_404_NOT_FOUND)

            logger.info("Initializing ChatMessage object")
            message = ChatMessage(
                sender=request.user,
                chat=chat_room,
                content=content,
                message_type=message_type,
                attachment=None,
                forwarded_from=forwarded_from
            )
            logger.info("Saving ChatMessage to database")
            message.save()
            logger.info(f"Message saved: id={message.id}")

            logger.info(f"Adding delivered_to for chat {chat_room.id}")
            members = chat_room.members.all()
            message.delivered_to.add(*members)
            logger.info(f"Delivered to: {[m.username for m in members]}")

            logger.info("Serializing message")
            serializer = ChatMessageSerializer(message, context={'request': request})
            response_data = serializer.data
            response_data["chat"] = {"id": str(chat_room.id)}
            logger.info(f"Message {message.id} serialized successfully: {response_data}")
            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Unexpected error in SendMessageView: {str(e)}", exc_info=True)
            return Response({"error": f"Server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
class GetMessagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, chat_id):
        try:
            chat_room = ChatRoom.objects.get(id=chat_id, members=request.user)
            messages = chat_room.messages.all().order_by("timestamp")
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
            chat_rooms = ChatRoom.objects.filter(members=request.user).order_by("-updated_at")
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
        chat_room.admins.add(request.user)
        logger.info(f"Created group chat {chat_room.id} with name {name}")

        message = ChatMessage.objects.create(
            sender=request.user,
            chat=chat_room,
            content=f"Group '{name}' created.",
            message_type="system"
        )
        message.delivered_to.add(*chat_room.members.all())

        serializer = ChatRoomSerializer(chat_room, context={'request': request})
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

@api_view(['POST'])
@csrf_exempt
def send_message(request):
    try:
        receiver_id = request.data.get('receiver_id')
        content = request.data.get('content', '')
        message_type = request.data.get('message_type', 'text')

        if not receiver_id:
            logger.warning("Receiver ID required in send_message")
            return Response({'error': 'Receiver ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        logger.info(f"Fetching receiver with ID {receiver_id}")
        try:
            receiver = User.objects.get(id=receiver_id)
            logger.info(f"Receiver found: id={receiver.id}, username={receiver.username}")
        except User.DoesNotExist:
            logger.error(f"Receiver with ID {receiver_id} not found")
            return Response({"error": "Receiver not found"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            logger.error(f"Invalid receiver_id: {receiver_id}, error: {str(e)}")
            return Response({"error": "Invalid receiver ID"}, status=status.HTTP_400_BAD_REQUEST)

        logger.info("Checking for existing chat room")
        chat_room = ChatRoom.objects.filter(
            is_group=False, members=request.user
        ).filter(members=receiver).first()
        if not chat_room:
            logger.info("No existing chat room found, creating new one")
            chat_room = ChatRoom.objects.create(name="", is_group=False)
            chat_room.members.add(request.user, receiver)
            chat_room.save()
            logger.info(f"New chat room created: id={chat_room.id}")

        logger.info("Creating message")
        message = ChatMessage.objects.create(
            chat=chat_room,
            sender=request.user,
            content=content,
            message_type=message_type
        )
        message.delivered_to.add(*chat_room.members.all())

        logger.info(f"Message {message.id} created for chat {chat_room.id}")
        serializer = ChatMessageSerializer(message, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    except Exception as e:
        logger.error(f"Error in send_message: {str(e)}", exc_info=True)
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)