# chat/urls.py
from django.urls import path
from . import views
from .views import ChatProfileView, GetMessagesView, UploadAttachmentView, MarkAsReadBatchView, ReactToMessageView

urlpatterns = [
    path('send-message/', views.SendMessageView.as_view(), name='send-message'),
    path('get-messages/<int:chat_id>/', GetMessagesView.as_view(), name='get_messages'),
    path('mark-as-read/<int:message_id>/', views.MarkAsReadView.as_view(), name='mark-as-read'),
    path('mark-as-read/batch/', MarkAsReadBatchView.as_view(), name='mark-as-read-batch'),
    path('rooms/', views.ChatRoomListView.as_view(), name='chat-rooms'),
    path('upload-attachment/<int:chat_id>/', UploadAttachmentView.as_view(), name='upload_attachment'),
    path('create-group-chat/', views.create_group_chat, name='create-group-chat'),
    path('edit-message/<int:message_id>/', views.edit_message, name='edit-message'),
    path('delete-message/<int:message_id>/', views.delete_message, name='delete-message'),
    path('pin-message/<int:chat_id>/<int:message_id>/', views.pin_message, name='pin-message'),
    path('manage-group-member/<int:chat_id>/', views.manage_group_member, name='manage-group-member'),
    path('profile/<str:chat_id>/', ChatProfileView.as_view(), name='chat-profile'),
    path('react-to-message/<int:message_id>/', ReactToMessageView.as_view(), name='react-to-message'),
]