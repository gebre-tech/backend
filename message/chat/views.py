from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Message
from .serializers import MessageSerializer
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
import cloudinary.uploader
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

class FileUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            if 'file' not in request.FILES:
                return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

            file = request.FILES['file']
            # Validate file size
            if file.size > 100 * 1024 * 1024:  # 100MB limit
                return Response({"error": "File must be under 100MB"}, status=status.HTTP_400_BAD_REQUEST)

            # Validate file type
            allowed_types = ['image/jpeg', 'image/png', 'video/mp4', 'audio/mpeg', 'application/pdf']
            if file.content_type not in allowed_types:
                return Response({"error": "Unsupported file type"}, status=status.HTTP_400_BAD_REQUEST)

            # Upload to Cloudinary
            upload_result = cloudinary.uploader.upload(
                file,
                folder=f"chat_files/user_{request.user.id}",
                public_id=f"file_{request.user.id}_{int(timezone.now().timestamp())}",
                overwrite=True,
                resource_type="auto"  # Automatically detect resource type (image, video, etc.)
            )

            return Response({
                "secure_url": upload_result['secure_url'],
                "public_id": upload_result['public_id']
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error uploading file to Cloudinary: {str(e)}", exc_info=True)
            return Response({"error": f"Failed to upload file: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MessageListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Retrieve all messages between sender and receiver."""
        sender_id = request.query_params.get('sender')
        receiver_id = request.query_params.get('receiver')

        if not sender_id or not receiver_id:
            return Response({"error": "Sender and Receiver IDs are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            messages = Message.objects.filter(
                (Q(sender_id=sender_id) & Q(receiver_id=receiver_id)) |
                (Q(sender_id=receiver_id) & Q(receiver_id=sender_id))
            ).order_by('created_at')
            serializer = MessageSerializer(messages, many=True, context={'request': request})
            return Response(serializer.data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def post(self, request):
        """Send a message and retrieve the full conversation between sender and receiver."""
        serializer = MessageSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            sender_id = request.data.get("sender")
            receiver_id = request.data.get("receiver")
            nonce = request.data.get("nonce")  # Optional nonce from request

            if not sender_id or not receiver_id:
                return Response({"error": "Sender and Receiver IDs are required."}, status=status.HTTP_400_BAD_REQUEST)

            sender = User.objects.get(id=sender_id)
            receiver = User.objects.get(id=receiver_id)

            # Create message with optional nonce
            message_data = {
                'sender': sender,
                'receiver': receiver,
                'content': serializer.validated_data['content'],
            }
            if 'file' in serializer.validated_data:
                message_data['file'] = serializer.validated_data['file']
                message_data['file_name'] = serializer.validated_data.get('file_name')
                message_data['file_type'] = serializer.validated_data.get('file_type')
            if nonce is not None:
                message_data['nonce'] = nonce

            Message.objects.create(**message_data)

            messages = Message.objects.filter(
                (Q(sender_id=sender_id) & Q(receiver_id=receiver_id)) |
                (Q(sender_id=receiver_id) & Q(receiver_id=sender_id))
            ).order_by('created_at')

            all_messages_serializer = MessageSerializer(messages, many=True, context={'request': request})
            return Response(all_messages_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)