# message/asgi.py
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import chat.routing
import contacts.routing
import profiles.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'message.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            chat.routing.websocket_urlpatterns +
            contacts.routing.websocket_urlpatterns +
            profiles.routing.websocket_urlpatterns
        )
    ),
})