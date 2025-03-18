# message/asgi.py
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from chat.routing import websocket_urlpatterns as chat_websocket_urlpatterns
from contacts.routing import websocket_urlpatterns as contacts_websocket_urlpatterns
from profiles.routing import websocket_urlpatterns as profiles_websocket_urlpatterns

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'message.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            chat_websocket_urlpatterns +
            contacts_websocket_urlpatterns +
            profiles_websocket_urlpatterns
        )
    ),
})