"""Signed links for email verification and password reset."""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

User = get_user_model()


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        verified = "1" if user.email_verified else "0"
        email = user.email or ""
        return f"{user.pk}{user.password}{timestamp}{email}{verified}"


email_verification_token = EmailVerificationTokenGenerator()
password_reset_token = PasswordResetTokenGenerator()


def user_uid(user) -> str:
    return urlsafe_base64_encode(force_bytes(user.pk))


def user_from_uid(uid: str):
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        return User.objects.get(pk=pk)
    except (TypeError, ValueError, OverflowError, ValidationError, User.DoesNotExist):
        return None


def frontend_url(path: str, query: dict[str, str]) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:4200").rstrip("/")
    return f"{base}{path}?{urlencode(query)}"
