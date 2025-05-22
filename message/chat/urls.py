# chat/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('messages/', views.MessageListView.as_view(), name='message-list'),
    path('upload/', views.FileUploadView.as_view(), name='file-upload'),
]