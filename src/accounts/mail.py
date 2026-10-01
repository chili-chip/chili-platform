"""Send verification and password-reset mail.

Local development (``manage.py``, and wrangler dev when ``.dev.vars`` sets
``EMAIL_BACKEND``) uses a Django mail backend, normally the console backend,
which prints the message to stdout. The deployed Worker sends with the Gmail
API over HTTPS. Workers cannot open ``smtp.gmail.com``. Django's SMTP default
means "no console override": post to
``https://gmail.googleapis.com/gmail/v1/users/me/messages/send`` with an
access token minted from ``GMAIL_REFRESH_TOKEN``.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.policy import EmailPolicy
from typing import Any

from django.conf import settings

from accounts.tokens import (
    email_verification_token,
    frontend_url,
    password_reset_token,
    user_uid,
)

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


class MailNotConfigured(Exception):
    """Gmail secrets are missing and this process is a Worker."""


class MailDeliveryError(Exception):
    """The Gmail API refused the message or the token exchange."""


def gmail_is_configured() -> bool:
    return all(
        str(getattr(settings, name, "") or "").strip()
        for name in (
            "GMAIL_CLIENT_ID",
            "GMAIL_CLIENT_SECRET",
            "GMAIL_REFRESH_TOKEN",
            "GMAIL_SENDER",
        )
    )


def uses_django_mail() -> bool:
    """True when EMAIL_BACKEND is an explicit non-SMTP Django backend.

    Console, locmem, and file backends count. Django's SMTP default does not:
    that is the deployed Worker, which sends through the Gmail API.
    """
    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "").strip()
    return bool(backend) and backend != SMTP_BACKEND


def send_verification_email(user) -> None:
    """Email a verification link. The link is not returned to the caller."""
    _refuse_unconfigured_worker()
    link = frontend_url(
        "/verify-email",
        {"uid": user_uid(user), "token": email_verification_token.make_token(user)},
    )
    body = (
        "Confirm your email for Chili Platform:\n\n"
        f"{link}\n\n"
        "If you did not create an account, you can ignore this message.\n"
    )
    _deliver(to=user.email, subject="Verify your Chili Platform email", body=body)


def send_password_reset_email(user) -> None:
    """Email a password reset link. The link is not returned to the caller."""
    _refuse_unconfigured_worker()
    link = frontend_url(
        "/reset-password",
        {"uid": user_uid(user), "token": password_reset_token.make_token(user)},
    )
    body = (
        "Reset your Chili Platform password:\n\n"
        f"{link}\n\n"
        "If you did not ask for this, you can ignore this message.\n"
    )
    _deliver(to=user.email, subject="Reset your Chili Platform password", body=body)


def _refuse_unconfigured_worker() -> None:
    if uses_django_mail():
        return
    if not gmail_is_configured() and getattr(settings, "ON_WORKERS", False):
        raise MailNotConfigured("Mail is not configured.")


def _deliver(*, to: str, subject: str, body: str) -> None:
    if uses_django_mail():
        _send_django(to=to, subject=subject, body=body)
        return
    if not gmail_is_configured():
        raise MailNotConfigured("Mail is not configured.")
    _send_gmail(to=to, subject=subject, body=body)


def _send_django(*, to: str, subject: str, body: str) -> None:
    from django.core.mail import send_mail

    sender = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "chili@localhost"
    send_mail(subject, body, sender, [to], fail_silently=False)


def _send_gmail(*, to: str, subject: str, body: str) -> None:
    token_payload = _post_form(
        GOOGLE_TOKEN_URL,
        {
            "client_id": settings.GMAIL_CLIENT_ID,
            "client_secret": settings.GMAIL_CLIENT_SECRET,
            "refresh_token": settings.GMAIL_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
    )
    access_token = token_payload.get("access_token")
    if not access_token:
        raise MailDeliveryError("Gmail did not return an access token.")
    _post_json(
        GMAIL_SEND_URL,
        {"raw": _raw_message(to=to, subject=subject, body=body)},
        headers={"Authorization": f"Bearer {access_token}"},
    )


def _raw_message(*, to: str, subject: str, body: str) -> str:
    # Keep the link on one line so mail clients don't fold it into an unclickable break.
    message = EmailMessage(policy=EmailPolicy(max_line_length=998))
    message["To"] = to
    message["From"] = settings.GMAIL_SENDER
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    return _request(
        "POST",
        url,
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        urllib.parse.urlencode(data),
    )


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str]) -> dict[str, Any]:
    merged = {"Content-Type": "application/json", "Accept": "application/json", **headers}
    return _request("POST", url, merged, json.dumps(payload))


def _request(method: str, url: str, headers: dict[str, str], body: str) -> dict[str, Any]:
    if getattr(settings, "ON_WORKERS", False):
        status, text = _workers_request(method, url, headers, body)
    else:
        status, text = _urllib_request(method, url, headers, body)
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise MailDeliveryError("Mail provider returned a non-JSON response.") from exc
    if status >= 400 or not isinstance(payload, dict):
        raise MailDeliveryError("Mail provider rejected the message.")
    return payload


def _urllib_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: str,
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise MailDeliveryError(f"Could not reach Gmail: {exc.reason}") from exc


def _workers_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: str,
) -> tuple[int, str]:
    from pyodide.ffi import run_sync
    from workers import fetch

    try:
        response = run_sync(fetch(url, method=method, headers=headers, body=body))
        text = run_sync(response.text())
    except MailDeliveryError:
        raise
    except Exception as exc:
        raise MailDeliveryError(f"Could not reach Gmail: {exc}") from exc
    return int(response.status), text
