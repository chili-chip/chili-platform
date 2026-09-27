"""Split a game price between Chili and the creator.

The creator's credit is the price minus 20% minus an estimate of Stripe
processing for that charge's currency and method. Separate charges and
transfers keep that cut by transferring less than the charge.
``application_fee_amount`` is not used.

The estimate is ``MARKETPLACE_PROCESSING_FEE_BPS`` plus
``MARKETPLACE_PROCESSING_FEE_FIXED_CENTS``. The operator sets both from
https://stripe.com/pricing. There is no default rate. Actual fees vary by
region, card, and method. Chili should watch the margin report; this number
is not a guaranteed profit.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_MISSING_ESTIMATE = (
    "Set MARKETPLACE_PROCESSING_FEE_BPS and MARKETPLACE_PROCESSING_FEE_FIXED_CENTS "
    "from https://stripe.com/pricing for the charge currency and method. "
    "There is no default card rate."
)


def percent_cents(amount: int, basis_points: int) -> int:
    """Round half up to the nearest cent."""
    if amount <= 0 or basis_points <= 0:
        return 0
    return (amount * basis_points + 5_000) // 10_000


def proportional(amount: int, numerator: int, denominator: int) -> int:
    if amount <= 0 or numerator <= 0 or denominator <= 0:
        return 0
    return (amount * numerator + denominator // 2) // denominator


def processing_estimate_settings() -> tuple[int, int]:
    """Basis points and fixed cents chosen by the operator. Neither has a default."""
    bps = getattr(settings, "MARKETPLACE_PROCESSING_FEE_BPS", None)
    fixed = getattr(settings, "MARKETPLACE_PROCESSING_FEE_FIXED_CENTS", None)
    if bps is None or fixed is None:
        raise ImproperlyConfigured(_MISSING_ESTIMATE)
    bps_i = int(bps)
    fixed_i = int(fixed)
    if bps_i < 0 or fixed_i < 0:
        raise ImproperlyConfigured(_MISSING_ESTIMATE)
    return bps_i, fixed_i


def split_price(price_cents: int) -> dict[str, int]:
    price = int(price_cents)
    if price <= 0:
        return {
            "platform_fee_cents": 0,
            "processing_estimate_cents": 0,
            "creator_credit_cents": 0,
        }
    bps, fixed = processing_estimate_settings()
    platform = percent_cents(price, int(settings.MARKETPLACE_PLATFORM_FEE_BPS))
    processing = percent_cents(price, bps) + fixed
    creator = price - platform - processing
    if creator < 0:
        creator = 0
    return {
        "platform_fee_cents": platform,
        "processing_estimate_cents": processing,
        "creator_credit_cents": creator,
    }
