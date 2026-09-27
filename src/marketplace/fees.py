"""Split a game price between Chili and the creator.

The creator's credit is the price minus 20% minus an estimate of Stripe card
processing. Separate charges and transfers keep that cut by transferring less
than the charge. ``application_fee_amount`` is not used.

The processing estimate is the published US online card rate, 2.9% + 30 cents
(https://stripe.com/pricing). Actual fees vary by region, card, and method.
Chili should watch the margin report; this number is not a guaranteed profit.
"""

from __future__ import annotations

from django.conf import settings


def percent_cents(amount: int, basis_points: int) -> int:
    """Round half up to the nearest cent."""
    if amount <= 0 or basis_points <= 0:
        return 0
    return (amount * basis_points + 5_000) // 10_000


def proportional(amount: int, numerator: int, denominator: int) -> int:
    if amount <= 0 or numerator <= 0 or denominator <= 0:
        return 0
    return (amount * numerator + denominator // 2) // denominator


def split_price(price_cents: int) -> dict[str, int]:
    price = int(price_cents)
    if price <= 0:
        return {
            "platform_fee_cents": 0,
            "processing_estimate_cents": 0,
            "creator_credit_cents": 0,
        }
    platform = percent_cents(price, int(settings.MARKETPLACE_PLATFORM_FEE_BPS))
    processing = percent_cents(price, int(settings.MARKETPLACE_PROCESSING_FEE_BPS)) + int(
        settings.MARKETPLACE_PROCESSING_FEE_FIXED_CENTS
    )
    creator = price - platform - processing
    if creator < 0:
        creator = 0
    return {
        "platform_fee_cents": platform,
        "processing_estimate_cents": processing,
        "creator_credit_cents": creator,
    }
