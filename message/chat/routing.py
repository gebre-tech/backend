#chat/routing.py is similar to profiles/consumers.py, but it is used to route WebSocket connections to the appropriate consumer class.
# The routing configuration is defined in the websocket_urlpatterns list, which maps URL patterns to consumer classes.
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<chat_id>\d+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r'ws/group_chat/(?P<chat_id>\d+)/$', consumers.GroupChatConsumer.as_asgi()),
    #re_path(r'ws/contacts/$', consumers.ContactsConsumer.as_asgi()),
]