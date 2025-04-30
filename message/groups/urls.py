from django.urls import path
from . import views

urlpatterns = [
    path('create/', views.CreateGroupView.as_view(), name='create_group'),
    path('message/send/', views.SendGroupMessageView.as_view(), name='send_group_message'),
    path('messages/<int:group_id>/', views.GetGroupMessagesView.as_view(), name='get_group_messages'),
    path('add_member/<int:group_id>/<int:user_id>/', views.AddMemberToGroupView.as_view(), name='add_member_to_group'),
    path('remove_member/<int:group_id>/<int:user_id>/', views.RemoveMemberFromGroupView.as_view(), name='remove_member_from_group'),
]