"""Record acceptance. Timestamps move only from null to now, never backward."""

from __future__ import annotations

from django.utils import timezone
from rest_framework.response import Response

TERMS_REQUIRED = "Accept the terms of service and privacy policy to create an account."
TERMS_BEFORE_SELLER = (
    "Accept the terms of service and privacy policy before the marketplace seller terms."
)
ACCEPTANCE_REQUIRED = (
    "Accept the terms of service and privacy policy, or the marketplace seller terms."
)
TERMS_BEFORE_ACTION = "Accept the terms of service and privacy policy before doing that."
SELLER_BEFORE_LISTING = "Accept the marketplace seller terms before listing a game."
SELLER_BEFORE_PAYOUT = "Accept the marketplace seller terms before payout setup."


def stamp_acceptance(user, *, terms: bool = False, seller_terms: bool = False) -> list[str]:
    now = timezone.now()
    updates: list[str] = []
    if terms:
        if user.terms_accepted_at is None:
            user.terms_accepted_at = now
            updates.append("terms_accepted_at")
        if user.privacy_accepted_at is None:
            user.privacy_accepted_at = now
            updates.append("privacy_accepted_at")
    if seller_terms:
        if user.seller_terms_accepted_at is None:
            user.seller_terms_accepted_at = now
            updates.append("seller_terms_accepted_at")
    if updates:
        user.save(update_fields=updates)
    return updates


def account_terms_accepted(user) -> bool:
    return bool(user.terms_accepted_at and user.privacy_accepted_at)


def seller_terms_block(user, *, payout: bool) -> Response | None:
    """403 until account terms and seller terms are both recorded."""
    if not account_terms_accepted(user):
        return Response({"detail": TERMS_BEFORE_ACTION}, status=403)
    if user.seller_terms_accepted_at is None:
        detail = SELLER_BEFORE_PAYOUT if payout else SELLER_BEFORE_LISTING
        return Response({"detail": detail}, status=403)
    return None
