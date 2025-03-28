# chat/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('send-message/', views.SendMessageView.as_view(), name='send-message'),
    path('get-messages/<int:chat_id>/', views.GetMessagesView.as_view(), name='get-messages'),
    path('mark-as-read/<int:message_id>/', views.MarkAsReadView.as_view(), name='mark-as-read'),
    path('rooms/', views.ChatRoomListView.as_view(), name='chat-rooms'),
    path('upload-attachment/<int:chat_id>/', views.upload_attachment, name='upload-attachment'),
    path('create-group-chat/', views.create_group_chat, name='create-group-chat'),
    path('edit-message/<int:message_id>/', views.edit_message, name='edit-message'),
    path('delete-message/<int:message_id>/', views.delete_message, name='delete-message'),
    path('pin-message/<int:chat_id>/<int:message_id>/', views.pin_message, name='pin-message'),
    path('manage-group-member/<int:chat_id>/', views.manage_group_member, name='manage-group-member'),
    path('send-message-simple/', views.send_message, name='send-message-simple'),  # Optional simpler endpoint
]