from django.urls import re_path
from .consumers import ChatConsumer

websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<sender_id>\d+)/(?P<receiver_id>\d+)/$', ChatConsumer.as_asgi()),  # ✅ Include both sender_id and receiver_id
]
