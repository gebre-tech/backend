from django.db import models
from django.conf import settings

class Message(models.Model):
    message_id = models.CharField(max_length=50, unique=True, null=True)  # Added for frontend message_id
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_messages', on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_messages', on_delete=models.CASCADE)
    content = models.TextField()  # Stores encrypted text for text messages
    file = models.FileField(upload_to='chat_files/', blank=True, null=True)  # For file uploads
    file_name = models.CharField(max_length=255, blank=True, null=True)  # Original file name
    file_type = models.CharField(max_length=100, blank=True, null=True)  # MIME type
    file_size = models.BigIntegerField(blank=True, null=True)  # Add file size field
    created_at = models.DateTimeField(auto_now_add=True)
    nonce = models.CharField(max_length=32, blank=True, null=True)  # For AES-256-CBC nonce (16 bytes in hex)
    ephemeral_key = models.CharField(max_length=64, blank=True, null=True)  # For NoiseNN ephemeral key (32 bytes in hex)
    handshake_key = models.CharField(max_length=64, blank=True, null=True)  # Store handshake public key (32 bytes in hex)
    message_key = models.CharField(max_length=64, blank=True, null=True)  # Store final message key (32 bytes in hex)

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