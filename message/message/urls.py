from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('authentication.urls')),
    path('chat/', include('chat.urls')),
    path('contacts/', include('contacts.urls')),
    path('groups/', include('groups.urls')),
    path('profiles/', include('profiles.urls')),
]

# Serve media files only during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)