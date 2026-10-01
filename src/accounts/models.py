from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Platform identity. Avatar lives in R2 via `avatar_url`."""

    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(
        default=False,
        help_text="Public registration starts false. Existing rows and the bootstrap admin are true.",
    )
    avatar_url = models.URLField(blank=True, default="")
    bio = models.TextField(blank=True, default="", max_length=500)
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    # Null until the person accepts. Existing rows stay null; do not backfill.
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    privacy_accepted_at = models.DateTimeField(null=True, blank=True)
    seller_terms_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["username"]

    def __str__(self) -> str:
        return self.username
