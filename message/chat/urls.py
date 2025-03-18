from django.urls import path
from . import views

urlpatterns = [
    path('send-message/', views.SendMessageView.as_view(), name='send-message'),
    path('get-messages/<int:chat_id>/', views.GetMessagesView.as_view(), name='get-messages'),
    path('mark-as-read/<int:message_id>/', views.MarkAsReadView.as_view(), name='mark-as-read'),
    path('rooms/', views.ChatRoomListView.as_view(), name='chat-rooms'),
    path('upload-attachment/<int:chat_id>/', views.upload_attachment, name='upload-attachment'),
    path('create-group-chat/', views.create_group_chat, name='create-group-chat'),
]