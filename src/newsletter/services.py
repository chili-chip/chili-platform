"""Subscribe, unsubscribe, and send newsletter issues in batches.

Recipients are confirmed public subscribers plus signed-in users with a
verified email who turned on ``newsletter_opt_in``. One address gets an
issue at most once (``Delivery`` is unique per issue and email). A Worker
request has a subrequest budget, so ``send_issue_batch`` sends at most
``NEWSLETTER_SEND_BATCH`` messages and reports how many remain.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.mail import MailDeliveryError, send_plain_email
from accounts.models import UserSettings
from accounts.tokens import frontend_url
from newsletter.models import Delivery, Issue, Subscriber
from newsletter.tokens import make_confirm_token, make_unsubscribe_token, normalize_email

User = get_user_model()


def request_subscription(email: str) -> bool:
    """Email a confirmation link. Returns False when the address is already subscribed."""
    email = normalize_email(email)
    subscriber = Subscriber.objects.filter(email=email).first()
    if subscriber is not None and subscriber.is_active:
        return False
    if subscriber is None:
        Subscriber.objects.create(email=email)
    link = frontend_url("/newsletter/confirm", {"token": make_confirm_token(email)})
    body = (
        "Confirm your subscription to the Chili Platform newsletter:\n\n"
        f"{link}\n\n"
        "If you did not ask for this, you can ignore this message.\n"
    )
    send_plain_email(to=email, subject="Confirm your Chili Platform newsletter subscription", body=body)
    return True


def confirm_subscription(email: str) -> Subscriber:
    email = normalize_email(email)
    subscriber, _ = Subscriber.objects.get_or_create(email=email)
    subscriber.confirmed_at = timezone.now()
    subscriber.unsubscribed_at = None
    subscriber.save(update_fields=["confirmed_at", "unsubscribed_at"])
    return subscriber


def unsubscribe(email: str) -> None:
    """Stop all newsletter mail to this address, from the form and from account settings."""
    email = normalize_email(email)
    now = timezone.now()
    Subscriber.objects.filter(email=email, unsubscribed_at__isnull=True).update(unsubscribed_at=now)
    UserSettings.objects.filter(user__email__iexact=email, newsletter_opt_in=True).update(
        newsletter_opt_in=False, newsletter_updated_at=now
    )


def recipient_emails() -> list[str]:
    """Every address that should get the next issue, lowercased, deduplicated, sorted."""
    emails = set(
        Subscriber.objects.filter(
            confirmed_at__isnull=False, unsubscribed_at__isnull=True
        ).values_list("email", flat=True)
    )
    emails.update(
        User.objects.filter(
            is_active=True, email_verified=True, settings__newsletter_opt_in=True
        ).values_list("email", flat=True)
    )
    return sorted({normalize_email(e) for e in emails if e})


def issue_body(issue: Issue, email: str) -> str:
    link = frontend_url("/newsletter/unsubscribe", {"token": make_unsubscribe_token(email)})
    return (
        f"{issue.body.rstrip()}\n\n"
        "--\n"
        "You get this because you subscribed to the Chili Platform newsletter.\n"
        f"Unsubscribe: {link}\n"
    )


def send_test(issue: Issue, email: str) -> None:
    send_plain_email(to=email, subject=f"[Test] {issue.subject}", body=issue_body(issue, email))


def send_issue_batch(issue: Issue, limit: int | None = None) -> dict[str, int]:
    """Send the next batch. MailNotConfigured propagates before anything is recorded."""
    if limit is None:
        limit = int(getattr(settings, "NEWSLETTER_SEND_BATCH", 50))
    if issue.sent_at is not None:
        return {"sent": 0, "failed": 0, "remaining": 0}
    if issue.sending_started_at is None:
        issue.sending_started_at = timezone.now()
        issue.save(update_fields=["sending_started_at"])

    handled = set(issue.deliveries.values_list("email", flat=True))
    pending = [email for email in recipient_emails() if email not in handled]
    sent = failed = 0
    for email in pending[:limit]:
        try:
            with transaction.atomic():
                delivery = Delivery.objects.create(issue=issue, email=email)
        except IntegrityError:
            # Another request claimed this address.
            continue
        try:
            send_plain_email(to=email, subject=issue.subject, body=issue_body(issue, email))
        except MailDeliveryError as exc:
            delivery.error = str(exc)[:500]
            delivery.save(update_fields=["error"])
            failed += 1
            continue
        except Exception:
            # Release the claim so a retry can pick the address up again.
            delivery.delete()
            raise
        delivery.sent_at = timezone.now()
        delivery.save(update_fields=["sent_at"])
        sent += 1

    remaining = max(len(pending) - limit, 0)
    if remaining == 0:
        issue.sent_at = timezone.now()
        issue.save(update_fields=["sent_at"])
    return {"sent": sent, "failed": failed, "remaining": remaining}
