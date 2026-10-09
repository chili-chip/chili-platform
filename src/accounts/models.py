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
    display_name = models.CharField(max_length=50, blank=True, default="")
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


class UserSettings(models.Model):
    """Per-user preferences and privacy switches. Created on first read."""

    class Theme(models.TextChoices):
        SYSTEM = "system", "System"
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="settings")
    locale = models.CharField(max_length=10, blank=True, default="en")
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)
    newsletter_opt_in = models.BooleanField(default=False)
    newsletter_updated_at = models.DateTimeField(null=True, blank=True)
    show_bio = models.BooleanField(default=True)
    show_joined = models.BooleanField(default=True)
    show_games = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "user settings"

    def __str__(self) -> str:
        return f"settings for {self.user_id}"


class SocialAccount(models.Model):
    """A GitHub or Google identity that can sign in as `user`."""

    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GOOGLE = "google", "Google"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="social_accounts")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    # GitHub's numeric user id or Google's `sub`. Stable across email and login changes.
    uid = models.CharField(max_length=191)
    email = models.EmailField(blank=True, default="")
    login = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["provider"]
        constraints = [
            models.UniqueConstraint(fields=["provider", "uid"], name="uniq_social_provider_uid"),
            models.UniqueConstraint(fields=["user", "provider"], name="uniq_social_user_provider"),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.uid}"
