"""Signed links for confirming and leaving the newsletter."""

from __future__ import annotations

from django.core import signing

CONFIRM_SALT = "newsletter.confirm"
CONFIRM_MAX_AGE = 60 * 60 * 24 * 7
UNSUBSCRIBE_SALT = "newsletter.unsubscribe"


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def make_confirm_token(email: str) -> str:
    return signing.dumps({"email": normalize_email(email)}, salt=CONFIRM_SALT)


def read_confirm_token(token: str) -> str | None:
    return _read(token, CONFIRM_SALT, CONFIRM_MAX_AGE)


def make_unsubscribe_token(email: str) -> str:
    """No expiry: an old newsletter's link must still work."""
    return signing.dumps({"email": normalize_email(email)}, salt=UNSUBSCRIBE_SALT)


def read_unsubscribe_token(token: str) -> str | None:
    return _read(token, UNSUBSCRIBE_SALT, None)


def _read(token: str, salt: str, max_age: int | None) -> str | None:
    try:
        data = signing.loads(token, salt=salt, max_age=max_age)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("email"), str):
        return None
    return data["email"] or None
