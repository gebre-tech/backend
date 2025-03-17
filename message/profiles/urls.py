#profiles/urls.py
# #profiles/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('profile/', views.CreateOrUpdateProfileView.as_view(), name='create_or_update_profile'),
    path('friend/<str:username>/', views.FriendProfileView.as_view(), name='friend_profile'),
    path('last_seen/', views.UpdateLastSeenView.as_view(), name='update_last_seen'),
]
