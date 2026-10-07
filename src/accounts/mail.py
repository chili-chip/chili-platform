"""Send verification and password-reset mail.

Local development (``manage.py``, and wrangler dev when ``.dev.vars`` sets
``EMAIL_BACKEND``) uses a Django mail backend, normally the console backend,
which prints the message to stdout. The deployed Worker sends through the
``EMAIL`` ``send_email`` binding of Cloudflare Email Sending, so there is no
API token to store or rotate. Django's SMTP default means "no console
override" and selects that path.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from accounts.tokens import (
    email_verification_token,
    frontend_url,
    password_reset_token,
    user_uid,
)

EMAIL_BINDING = "EMAIL"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


class MailNotConfigured(Exception):
    """The Worker has no email binding or sender address."""


class MailDeliveryError(Exception):
    """The email binding refused the message."""


def _email_binding() -> Any | None:
    """The Worker's ``send_email`` binding, or None when it is not bound."""
    try:
        from workers import env as worker_env

        return getattr(worker_env, EMAIL_BINDING, None)
    except Exception:
        return None


def mail_is_configured() -> bool:
    sender = str(getattr(settings, "EMAIL_FROM", "") or "").strip()
    return bool(sender) and _email_binding() is not None


def uses_django_mail() -> bool:
    """True when EMAIL_BACKEND is an explicit non-SMTP Django backend.

    Console, locmem, and file backends count. Django's SMTP default does not:
    that is the deployed Worker, which sends through the Cloudflare email binding.
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
    _send_binding(to=to, subject=subject, body=body)


def _send_django(*, to: str, subject: str, body: str) -> None:
    from django.core.mail import send_mail

    sender = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "chili@localhost"
    send_mail(subject, body, sender, [to], fail_silently=False)


def _send_binding(*, to: str, subject: str, body: str) -> None:
    message = {
        "to": to,
        "from": settings.EMAIL_FROM,
        "subject": subject,
        # Plain text keeps the link on one line for mail clients.
        "text": body,
    }
    try:
        _call_binding(_email_binding(), message)
    except Exception as exc:
        code = getattr(exc, "code", "") or type(exc).__name__
        raise MailDeliveryError(f"Email binding rejected the message ({code}).") from exc


def _call_binding(binding: Any, message: dict[str, str]) -> Any:
    """Call ``env.EMAIL.send`` from synchronous Django code on the Worker."""
    from js import Object
    from pyodide.ffi import run_sync, to_js

    return run_sync(binding.send(to_js(message, dict_converter=Object.fromEntries)))
