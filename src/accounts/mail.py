"""Send verification and password-reset mail.

Local development (``manage.py``, and wrangler dev when ``.dev.vars`` sets
``EMAIL_BACKEND``) uses a Django mail backend, normally the console backend,
which prints the message to stdout. The deployed Worker sends with the
Cloudflare Email Sending REST API: ``POST /accounts/{id}/email/sending/send``
with an API token that has the Email Sending: Edit permission. Django's SMTP
default means "no console override" and selects that path.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from accounts.tokens import (
    email_verification_token,
    frontend_url,
    password_reset_token,
    user_uid,
)

logger = logging.getLogger(__name__)

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


class MailNotConfigured(Exception):
    """Cloudflare mail settings are missing and this process is a Worker."""


class MailDeliveryError(Exception):
    """Cloudflare Email Sending refused the message or could not be reached."""


def mail_is_configured() -> bool:
    return all(
        str(getattr(settings, name, "") or "").strip()
        for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_EMAIL_API_TOKEN", "EMAIL_FROM")
    )


def send_url() -> str:
    return f"{CLOUDFLARE_API}/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}/email/sending/send"


def uses_django_mail() -> bool:
    """True when EMAIL_BACKEND is an explicit non-SMTP Django backend.

    Console, locmem, and file backends count. Django's SMTP default does not:
    that is the deployed Worker, which sends through Cloudflare Email Sending.
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
    if not mail_is_configured() and getattr(settings, "ON_WORKERS", False):
        raise MailNotConfigured("Mail is not configured.")


def _deliver(*, to: str, subject: str, body: str) -> None:
    if uses_django_mail():
        _send_django(to=to, subject=subject, body=body)
        return
    if not mail_is_configured():
        raise MailNotConfigured("Mail is not configured.")
    _send_cloudflare(to=to, subject=subject, body=body)


def _send_django(*, to: str, subject: str, body: str) -> None:
    from django.core.mail import send_mail

    sender = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "chili@localhost"
    send_mail(subject, body, sender, [to], fail_silently=False)


def _send_cloudflare(*, to: str, subject: str, body: str) -> None:
    payload = _request(
        "POST",
        send_url(),
        {
            "Authorization": f"Bearer {settings.CLOUDFLARE_EMAIL_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json.dumps(
            {
                "to": to,
                "from": settings.EMAIL_FROM,
                "subject": subject,
                # Plain text keeps the link on one line for mail clients.
                "text": body,
            }
        ),
    )
    result = payload.get("result")
    bounced = result.get("permanent_bounces") if isinstance(result, dict) else None
    if payload.get("success") is not True or bounced:
        raise MailDeliveryError("Mail provider rejected the message.")


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
        # The response carries Cloudflare's error codes, never the token.
        logger.error("Email Sending API returned %s: %s", status, text[:500])
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
        raise MailDeliveryError(f"Could not reach the mail provider: {exc.reason}") from exc


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
        raise MailDeliveryError(f"Could not reach the mail provider: {exc}") from exc
    return int(response.status), text
