# authentication/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=150, unique=True)
    password = models.CharField(max_length=128)
    first_name = models.CharField(max_length=150, blank=False, null=False)  # Make required
    last_name = models.CharField(max_length=150, blank=False, null=False)   # Make required

    def __str__(self):
        return self.email