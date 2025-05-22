from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Message
from .serializers import MessageSerializer
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated

User = get_user_model()

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
        """Send a message or file and retrieve the full conversation."""
        serializer = MessageSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            sender_id = request.data.get("sender")
            receiver_id = request.data.get("receiver")
            nonce = request.data.get("nonce")
            file_data = request.FILES.get("file")
            file_name = request.data.get("file_name")
            file_type = request.data.get("file_type")
            file_size = request.data.get("file_size")
            message_type = request.data.get("type", "text")
            message_id = request.data.get("message_id")

            if not sender_id or not receiver_id or not message_id:
                return Response({"error": "Sender, Receiver IDs, and message_id are required."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                sender = User.objects.get(id=sender_id)
                receiver = User.objects.get(id=receiver_id)
            except User.DoesNotExist:
                return Response({"error": "User does not exist."}, status=status.HTTP_400_BAD_REQUEST)

            message_data = {
                'sender': sender,
                'receiver': receiver,
                'content': serializer.validated_data.get('content', ''),
                'nonce': nonce or '',
                'ephemeral_key': serializer.validated_data.get('ephemeral_key', ''),
                'message_key': serializer.validated_data.get('message_key', ''),
                'type': message_type,
                'message_id': message_id
            }

            if file_data:
                message_data['file_name'] = file_name or file_data.name
                message_data['file_type'] = file_type or file_data.content_type
                message_data['file_size'] = file_size or file_data.size
                message_data['type'] = message_type or ('photo' if file_type.startswith('image/') else
                                                      'video' if file_type.startswith('video/') else
                                                      'audio' if file_type.startswith('audio/') else 'file')

            message = Message.objects.create(**message_data)
            if file_data:
                message.file.save(file_data.name, file_data)
                message.save()

            messages = Message.objects.filter(
                (Q(sender_id=sender_id) & Q(receiver_id=receiver_id)) |
                (Q(sender_id=receiver_id) & Q(receiver_id=sender_id))
            ).order_by('created_at')

            all_messages_serializer = MessageSerializer(messages, many=True, context={'request': request})
            return Response(all_messages_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)