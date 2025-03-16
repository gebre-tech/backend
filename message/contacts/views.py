# contacts/views.py
from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Contact, FriendRequest
from .serializers import ContactSerializer, FriendRequestSerializer
from authentication.models import User
from authentication.serializers import UserSerializer
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db import DatabaseError, IntegrityError
from django.db.models import Q

class CustomPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class SendFriendRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        username = request.data.get('username')
        if not username:
            return Response({"error": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            receiver = User.objects.get(username=username)
            if receiver == request.user:
                return Response({"error": "You cannot send a request to yourself"}, status=status.HTTP_400_BAD_REQUEST)

            if FriendRequest.objects.filter(sender=request.user, receiver=receiver, accepted=False).exists():
                return Response({"error": "Request already sent"}, status=status.HTTP_400_BAD_REQUEST)

            if Contact.objects.filter(user=request.user, friend=receiver).exists():
                return Response({"error": "Already friends"}, status=status.HTTP_400_BAD_REQUEST)

            friend_request = FriendRequest(sender=request.user, receiver=receiver)
            friend_request.save()
            serializer = FriendRequestSerializer(friend_request)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except User.DoesNotExist:
            return Response({"error": f"User '{username}' not found"}, status=status.HTTP_404_NOT_FOUND)

class GetFriendRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        requests = FriendRequest.objects.filter(receiver=request.user, accepted=False)
        serializer = FriendRequestSerializer(requests, many=True)
        return Response(serializer.data)

class AcceptFriendRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        try:
            friend_request = FriendRequest.objects.get(id=request_id, receiver=request.user, accepted=False)
            friend_request.accepted = True
            friend_request.save()

            # Create mutual contacts
            Contact.objects.create(user=request.user, friend=friend_request.sender)
            Contact.objects.create(user=friend_request.sender, friend=request.user)

            return Response({"status": "Friend request accepted"}, status=status.HTTP_200_OK)
        except FriendRequest.DoesNotExist:
            return Response({"error": "Friend request not found"}, status=status.HTTP_404_NOT_FOUND)

class RejectFriendRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        try:
            friend_request = FriendRequest.objects.get(id=request_id, receiver=request.user, accepted=False)
            friend_request.delete()  # Delete the request instead of marking it
            return Response({"status": "Friend request rejected"}, status=status.HTTP_200_OK)
        except FriendRequest.DoesNotExist:
            return Response({"error": "Friend request not found"}, status=status.HTTP_404_NOT_FOUND)

class AddFriendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            username = request.data.get('username')
            print(f"Received request to add friend: {username}")
            if not username:
                return Response({"error": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)

            friend = User.objects.get(username=username)
            print(f"Found friend: {friend.username} (ID: {friend.id})")

            if friend == request.user:
                return Response({"error": "You cannot add yourself as a friend"}, status=status.HTTP_400_BAD_REQUEST)

            if Contact.objects.filter(user=request.user, friend=friend).exists():
                return Response({"error": "Already friends"}, status=status.HTTP_400_BAD_REQUEST)

            contact = Contact(user=request.user, friend=friend)
            print(f"Contact instance created: {contact} (User ID: {request.user.id}, Friend ID: {friend.id})")
            contact.save()
            print(f"Contact saved successfully: {contact.id}")

            serializer = ContactSerializer(contact, context={'request': request})
            print("Serializer data prepared:", serializer.data)

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except User.DoesNotExist:
            print(f"User not found: {username}")
            return Response({"error": f"User '{username}' not found"}, status=status.HTTP_404_NOT_FOUND)
        except IntegrityError as e:
            print(f"Database IntegrityError: {str(e)}")
            return Response({"error": f"Database constraint failed: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
        except DatabaseError as e:
            print(f"Database error: {str(e)}")
            return Response({"error": "Database error occurred"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(f"Unexpected error in AddFriendView: {type(e).__name__}: {str(e)}")
            return Response({"error": f"Server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class GetContactsView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPagination

    def get(self, request):
        try:
            contacts = Contact.objects.filter(user=request.user).order_by('-created_at')
            paginator = self.pagination_class()
            paginated_contacts = paginator.paginate_queryset(contacts, request)
            serializer = ContactSerializer(paginated_contacts, many=True)
            return paginator.get_paginated_response(serializer.data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SearchContactsView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPagination

    def get(self, request):
        try:
            query = request.query_params.get('query', '')
            contacts = Contact.objects.filter(
                user=request.user,
                friend__username__icontains=query
            ).order_by('friend__username')
            
            paginator = self.pagination_class()
            paginated_contacts = paginator.paginate_queryset(contacts, request)
            serializer = ContactSerializer(paginated_contacts, many=True)
            return paginator.get_paginated_response(serializer.data)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SearchUsersView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPagination

    def get(self, request):
        try:
            query = request.query_params.get('query', '')
            if not query:
                return Response({"error": "Query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

            users = User.objects.filter(
                Q(username__icontains=query) | 
                Q(email__icontains=query)
            ).exclude(id=request.user.id)
            
            paginator = self.pagination_class()
            paginated_users = paginator.paginate_queryset(users, request)
            serializer = UserSerializer(paginated_users, many=True)
            return Response({
                'count': paginator.page.paginator.count,
                'results': serializer.data
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SentFriendRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sent_requests = FriendRequest.objects.filter(sender=request.user, accepted=False)
        serializer = FriendRequestSerializer(sent_requests, many=True)
        return Response(serializer.data)