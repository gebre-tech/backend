from django.db import models
from django.conf import settings
import cloudinary.uploader

class Message(models.Model):
    message_id = models.CharField(max_length=50, unique=True, null=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_messages', on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_messages', on_delete=models.CASCADE)
    content = models.TextField(blank=True)  # For encrypted text messages
    file = models.FileField(upload_to='chat_files/%Y/%m/%d/', blank=True, null=True)  # Cloudinary storage
    file_name = models.CharField(max_length=255, blank=True, null=True)
    file_type = models.CharField(max_length=100, blank=True, null=True)
    file_size = models.BigIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    nonce = models.CharField(max_length=32, blank=True, null=True)
    ephemeral_key = models.CharField(max_length=64, blank=True, null=True)
    handshake_key = models.CharField(max_length=64, blank=True, null=True)
    message_key = models.CharField(max_length=64, blank=True, null=True)
    type = models.CharField(max_length=20, default='text')

    def __str__(self):
        base_str = f"{self.sender.username} -> {self.receiver.username} (ID: {self.message_id})"
        if self.handshake_key:
            return f"{base_str}: Handshake (key: {self.handshake_key})"
        if self.content and self.file:
            return f"{base_str}: {self.content} (with file: {self.file_name}, nonce: {self.nonce}, ephemeral_key: {self.ephemeral_key}, message_key: {self.message_key})"
        elif self.file:
            return f"{base_str}: File: {self.file_name} (type: {self.file_type}, size: {self.file_size})"
        elif self.content:
            return f"{base_str}: {self.content} (nonce: {self.nonce}, ephemeral_key: {self.ephemeral_key}, message_key: {self.message_key})"
        return base_str

    def save(self, *args, **kwargs):
        if self.file and not self.file_name:
            self.file_name = self.file.name.split('/')[-1]
        if self.file and not self.file_type:
            self.file_type = 'application/octet-stream'  # Default MIME type
        if self.file and not self.file_size:
            self.file_size = self.file.size
        super().save(*args, **kwargs)