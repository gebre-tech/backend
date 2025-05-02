from django.db import models
from authentication.models import User

class Group(models.Model):
    name = models.CharField(max_length=255)
    creator = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="created_groups"
    )  # Track the original creator (owner)
    admins = models.ManyToManyField(
        User, related_name="admin_groups"
    )  # Track all admins (including creator)
    members = models.ManyToManyField(User, related_name="group_memberships")
    created_at = models.DateTimeField(auto_now_add=True)
    profile_picture = models.ImageField(upload_to="group_profiles/", blank=True, null=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Ensure the creator is always in the admins list
        super().save(*args, **kwargs)
        if self.creator and self.creator not in self.admins.all():
            self.admins.add(self.creator)

class GroupMessage(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField(blank=True, null=True)
    attachment = models.FileField(upload_to="group_attachments/", blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    reactions = models.JSONField(default=dict)
    read_by = models.ManyToManyField(User, related_name="read_group_messages")

    def __str__(self):
        return f"{self.sender} in {self.group.name}: {self.message[:30]}"