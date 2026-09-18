from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Platform identity. Avatar lives in R2 via `avatar_url`."""

    email = models.EmailField(unique=True)
    avatar_url = models.URLField(blank=True, default="")
    bio = models.TextField(blank=True, default="", max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["username"]

    def __str__(self) -> str:
        return self.username
