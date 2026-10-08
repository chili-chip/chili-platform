from __future__ import annotations

from django.conf import settings
from django.db import models


class Subscriber(models.Model):
    """An address that asked for the newsletter through the public form.

    Signed-in users subscribe with ``UserSettings.newsletter_opt_in`` instead.
    A row is active once confirmed and until it unsubscribes.
    """

    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.email

    @property
    def is_active(self) -> bool:
        return self.confirmed_at is not None and self.unsubscribed_at is None


class Issue(models.Model):
    """One newsletter email. Sent in batches; ``sent_at`` is set after the last batch."""

    subject = models.CharField(max_length=200)
    body = models.TextField(max_length=20000, help_text="Plain text. An unsubscribe link is added.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="newsletter_issues",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sending_started_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.subject


class Delivery(models.Model):
    """One address for one issue. The unique pair stops a second send."""

    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="deliveries")
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "deliveries"
        constraints = [
            models.UniqueConstraint(fields=["issue", "email"], name="uniq_issue_email"),
        ]

    def __str__(self) -> str:
        return f"{self.issue_id} -> {self.email}"
