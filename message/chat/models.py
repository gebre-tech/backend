from django.db import models
from django.conf import settings

class ChatRoom(models.Model):
    name = models.CharField(max_length=255, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="chat_rooms")
    created_at = models.DateTimeField(auto_now_add=True)
    is_group = models.BooleanField(default=False)

    def __str__(self):
        return self.name or f"Direct Chat {self.id}"

class ChatMessage(models.Model):
    MESSAGE_TYPES = (
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('file', 'File'),
    )
    
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')
    chat = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    content = models.TextField(blank=True, null=True)
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES, default='text')
    attachment = models.FileField(upload_to="chat_attachments/", blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    seen_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="seen_messages", blank=True)
    delivered_to = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="delivered_messages", blank=True)

    class Meta:
        ordering = ['-timestamp']

    def unread_count(self, user):
        return self.chat.messages.exclude(seen_by=user).count()

    def __str__(self):
        return f"{self.sender.username}: {self.content or self.message_type} in {self.chat}"