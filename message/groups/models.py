# groups/models.py
from django.db import models
from authentication.models import User

class Group(models.Model):
    name = models.CharField(max_length=255)
    admin = models.ForeignKey(User, on_delete=models.CASCADE, related_name="admin_groups")
    members = models.ManyToManyField(User, related_name="group_memberships")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class GroupMessage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField(blank=True, null=True)
    attachment = models.FileField(upload_to="group_attachments/", blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    reactions = models.JSONField(default=dict)  # e.g., {"user_id": "reaction"}
    read_by = models.ManyToManyField(User, related_name="read_group_messages")  # Track who read the message

    def __str__(self):
        return f"{self.sender} in {self.group.name}: {self.message[:30]}"