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