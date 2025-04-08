# chat/models.py
from django.db import models
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
import mimetypes

class ChatRoom(models.Model):
    name = models.CharField(max_length=255, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="chat_rooms")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_group = models.BooleanField(default=False)
    admins = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="admin_chat_rooms", blank=True
    )
    pinned_message = models.OneToOneField(
        'ChatMessage', null=True, blank=True, on_delete=models.SET_NULL, related_name="pinned_in"
    )

    def __str__(self):
        return self.name or f"Direct Chat {self.id}"

    def add_member(self, user, added_by=None):
        self.members.add(user)
        if added_by:
            ChatMessage.objects.create(
                chat=self,
                sender=added_by,
                content=f"{user.username} was added to the group.",
                message_type="system"
            )

    def remove_member(self, user, removed_by=None):
        self.members.remove(user)
        if removed_by:
            ChatMessage.objects.create(
                chat=self,
                sender=removed_by,
                content=f"{user.username} was removed from the group.",
                message_type="system"
            )

class ChatMessage(models.Model):
    MESSAGE_TYPES = (
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('file', 'File'),  # Generic file type for PDFs, docs, etc.
        ('system', 'System'),
    )
    
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages'
    )
    chat = models.ForeignKey(
        'ChatRoom',
        on_delete=models.CASCADE,
        related_name='messages'
    )
    content = models.TextField(blank=True, null=True)
    message_type = models.CharField(
        max_length=10,
        choices=MESSAGE_TYPES,
        default='text'
    )
    attachment = models.FileField(
        upload_to="chat_attachments/%Y/%m/%d/",
        blank=True,
        null=True
    )
    attachment_mime_type = models.CharField(
        max_length=100,
        blank=True,
        null=True
    )  # Store MIME type
    attachment_size = models.PositiveBigIntegerField(
        null=True,
        blank=True
    )  # Store file size in bytes, using BigInteger for larger files
    attachment_url = models.URLField(
        blank=True,
        null=True
    )
    attachment_name = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )  # Store original file name
    timestamp = models.DateTimeField(default=timezone.now)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)
    forwarded_from = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='forwards'
    )
    seen_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="seen_messages",
        blank=True,
        through='MessageSeen'
    )
    delivered_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="delivered_messages",
        blank=True
    )
    reactions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['timestamp']
        unique_together = ('chat', 'sender', 'content', 'message_type', 'timestamp')

    def save(self, *args, **kwargs):
        if self.attachment:
            # Generate attachment_url
            self.attachment_url = self.attachment.url
            # Store original file name
            if not self.attachment_name:
                self.attachment_name = self.attachment.name.split('/')[-1]
            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(self.attachment_name)
            self.attachment_mime_type = (
                mime_type or
                getattr(self.attachment.file, 'content_type', 'application/octet-stream')
            )
            # Store file size
            self.attachment_size = self.attachment.size
            # Set message_type based on MIME type if not explicitly set
            if not self.message_type or self.message_type == 'text':
                mime = self.attachment_mime_type.lower()
                if mime.startswith('image/'):
                    self.message_type = 'image'
                elif mime.startswith('video/'):
                    self.message_type = 'video'
                elif mime.startswith('audio/'):
                    self.message_type = 'audio'
                else:
                    self.message_type = 'file'
        try:
            super().save(*args, **kwargs)
        except IntegrityError:
            # Handle duplicate message
            existing_message = ChatMessage.objects.get(
                chat=self.chat,
                sender=self.sender,
                content=self.content,
                message_type=self.message_type,
                timestamp=self.timestamp
            )
            return existing_message

    def unread_count(self, user):
        return self.chat.messages.exclude(seen_by=user).count()

    def edit(self, new_content):
        self.content = new_content
        self.edited_at = timezone.now()
        self.save()

    def delete(self):
        self.is_deleted = True
        self.content = "[Message Deleted]"
        self.save()

    def __str__(self):
        return f"{self.sender.username}: {self.content or self.message_type} in {self.chat}"
class MessageSeen(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE)
    seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'message')