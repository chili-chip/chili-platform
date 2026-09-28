"""Game sales, creator balances, and the $20 payout."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, FloatField, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from games.models import Game
from marketplace.fees import proportional, split_price
from marketplace.models import (
    Category,
    ConnectedAccount,
    Earning,
    Listing,
    ListingTag,
    Payout,
    Purchase,
    Rating,
)
from marketplace.payments import (
    cancel_checkout,
    capability_status,
    create_connected_account,
    fetch_checkout,
    game_checkout_params,
    payouts_active,
    refund_payment_intent,
    retrieve_connected_account,
    reverse_transfer,
    send_transfer,
    start_checkout,
    transfers_active,
)
from store.stripe import StripeError

_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}\Z")
ACTIVE_PURCHASE = (
    Purchase.Status.PENDING,
    Purchase.Status.PAID,
    Purchase.Status.REFUNDED,
    Purchase.Status.DISPUTED,
)
REASON_SETUP = "Connect payouts before Chili can send earnings."
REASON_TRANSFERS = "Transfers are not active."
REASON_PAYOUTS = "Payouts are not active."
REASON_HOLD = "Earnings are in the 7-day hold."
REASON_MINIMUM = "Cleared earnings are under $20."
REASON_NONE = "No cleared earnings yet."


def min_paid_cents() -> int:
    return int(settings.MARKETPLACE_MIN_PAID_CENTS)


def min_payout_cents() -> int:
    return int(settings.MARKETPLACE_MIN_PAYOUT_CENTS)


def hold_days() -> int:
    return int(settings.MARKETPLACE_HOLD_DAYS)


def normalize_tags(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = str(raw).strip().lower().replace(" ", "-")
        if not tag:
            continue
        if not _TAG.match(tag):
            raise ValidationError(
                {"tags": [f"'{raw}' must use letters, numbers, and hyphens."]}
            )
        if tag not in seen:
            seen.add(tag)
            cleaned.append(tag)
    if len(cleaned) > 8:
        raise ValidationError({"tags": ["Use at most 8 tags."]})
    return cleaned


def _replace_tags(listing: Listing, names: list[str]) -> None:
    listing.tags.all().delete()
    ListingTag.objects.bulk_create([ListingTag(listing=listing, name=name) for name in names])


def _category(slug: str) -> Category:
    category = Category.objects.filter(slug=slug).first()
    if category is None:
        raise ValidationError({"category": "Pick a category."})
    return category


def _price_or_error(price_cents: int) -> int:
    price = int(price_cents)
    if price < 0:
        raise ValidationError({"price_cents": "Price cannot be negative."})
    if price != 0 and price < min_paid_cents():
        raise ValidationError({"price_cents": "Paid games must cost at least $1."})
    return price


def create_listing(user, data: dict) -> Listing:
    game = Game.objects.filter(pk=data["game"]).first()
    if game is None:
        raise ValidationError({"game": "Save the game before listing it."})
    if game.owner_id != user.id:
        raise ValidationError({"game": "You can only list your own games."})
    if not game.released:
        raise ValidationError({"game": "Release the game before listing it."})
    if Listing.objects.filter(game=game).exists():
        raise ValidationError({"game": "This game is already listed."})
    price = _price_or_error(data["price_cents"])
    category = _category(data["category"])
    tags = normalize_tags(data.get("tags"))
    listing = Listing.objects.create(
        game=game,
        seller=user,
        slug=game.slug,
        description=(data.get("description") or "").strip(),
        price_cents=price,
        currency=getattr(settings, "MARKETPLACE_CURRENCY", "usd"),
        category=category,
        published=bool(data.get("published", True)),
    )
    _replace_tags(listing, tags)
    return listing


def update_listing(listing: Listing, data: dict) -> Listing:
    if "price_cents" in data:
        listing.price_cents = _price_or_error(data["price_cents"])
    if "description" in data:
        listing.description = (data.get("description") or "").strip()
    if "category" in data:
        listing.category = _category(data["category"])
    if "published" in data:
        listing.published = bool(data["published"])
    listing.save()
    if "tags" in data:
        _replace_tags(listing, normalize_tags(data.get("tags")))
    return listing


def unpublish_or_delete(listing: Listing) -> Listing | None:
    if listing.purchases.exclude(status__in=[Purchase.Status.FAILED, Purchase.Status.CANCELED]).exists():
        listing.published = False
        listing.save(update_fields=["published", "updated_at"])
        return listing
    listing.delete()
    return None


def _lock(queryset):
    if not getattr(settings, "ON_WORKERS", False):
        queryset = queryset.select_for_update()
    return queryset


def _active_purchase(buyer, game) -> Purchase | None:
    if game is None:
        return None
    return (
        Purchase.objects.filter(buyer=buyer, game=game, status__in=ACTIVE_PURCHASE)
        .order_by("-id")
        .first()
    )


def _new_purchase(buyer, listing) -> Purchase:
    split = split_price(listing.price_cents)
    return Purchase.objects.create(
        listing=listing,
        game=listing.game,
        buyer=buyer,
        seller=listing.seller,
        title=listing.game.title[:160],
        price_cents=listing.price_cents,
        currency=listing.currency,
        platform_fee_cents=split["platform_fee_cents"],
        processing_estimate_cents=split["processing_estimate_cents"],
        creator_credit_cents=split["creator_credit_cents"],
        status=Purchase.Status.PENDING,
    )


def _capture_ids(purchase: Purchase, session: dict) -> None:
    payment_intent = session.get("payment_intent")
    charge = None
    if isinstance(payment_intent, dict):
        purchase.stripe_payment_intent_id = payment_intent.get("id") or purchase.stripe_payment_intent_id
        charge = payment_intent.get("latest_charge")
        if isinstance(charge, dict):
            charge = charge.get("id")
    elif payment_intent:
        purchase.stripe_payment_intent_id = str(payment_intent)
    if isinstance(charge, str) and charge:
        purchase.stripe_charge_id = charge
    session_id = session.get("id") or ""
    if session_id:
        purchase.stripe_checkout_session_id = session_id


def _credit_creator(purchase: Purchase) -> None:
    if purchase.creator_credit_cents <= 0:
        return
    if Earning.objects.filter(purchase=purchase).exists():
        return
    paid_at = purchase.paid_at or timezone.now()
    Earning.objects.create(
        purchase=purchase,
        creator=purchase.seller,
        credit_cents=purchase.creator_credit_cents,
        unpaid_cents=purchase.creator_credit_cents,
        status=Earning.Status.HELD,
        available_at=paid_at + timedelta(days=hold_days()),
    )


def mark_purchase_paid(purchase: Purchase, session: dict) -> Purchase:
    _capture_ids(purchase, session)
    if purchase.status == Purchase.Status.PENDING:
        purchase.status = Purchase.Status.PAID
        purchase.paid_at = timezone.now()
        purchase.save()
        _credit_creator(purchase)
        return purchase
    purchase.save()
    return purchase


def _customer_id(user) -> str:
    if not getattr(settings, "STRIPE_SYNC_ENABLED", True):
        return ""
    try:
        from store.sync import ensure_stripe_customer

        return ensure_stripe_customer(user) or ""
    except StripeError:
        return ""


def _checkout_urls() -> tuple[str, str]:
    success = getattr(
        settings,
        "MARKETPLACE_CHECKOUT_SUCCESS_URL",
        "http://localhost:4200/marketplace/checkout/success?session_id={CHECKOUT_SESSION_ID}",
    )
    cancel = getattr(
        settings,
        "MARKETPLACE_CHECKOUT_CANCEL_URL",
        "http://localhost:4200/marketplace/checkout/cancel",
    )
    return success, cancel


def claim_or_checkout(user, listing: Listing) -> tuple[Purchase, dict | None]:
    if not listing.published:
        raise ValidationError({"detail": "This game is not for sale."})
    if listing.seller_id == user.id:
        raise ValidationError({"detail": "You can't buy your own game."})
    existing = _active_purchase(user, listing.game)
    if existing and existing.status != Purchase.Status.PENDING:
        raise ValidationError({"detail": "You already have this game in your library."})

    if listing.price_cents == 0:
        if existing and existing.status == Purchase.Status.PENDING:
            existing.status = Purchase.Status.CANCELED
            existing.save(update_fields=["status", "updated_at"])
        purchase = _new_purchase(user, listing)
        purchase.status = Purchase.Status.PAID
        purchase.paid_at = timezone.now()
        purchase.save(update_fields=["status", "paid_at", "updated_at"])
        return purchase, None

    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise StripeError("Stripe is not configured.", status_code=503)

    purchase = existing or _new_purchase(user, listing)
    if purchase.price_cents != listing.price_cents or purchase.status != Purchase.Status.PENDING:
        purchase.price_cents = listing.price_cents
        split = split_price(listing.price_cents)
        purchase.platform_fee_cents = split["platform_fee_cents"]
        purchase.processing_estimate_cents = split["processing_estimate_cents"]
        purchase.creator_credit_cents = split["creator_credit_cents"]
        purchase.currency = listing.currency
        purchase.status = Purchase.Status.PENDING
        purchase.save()

    if purchase.stripe_checkout_session_id:
        current = None
        try:
            current = fetch_checkout(purchase.stripe_checkout_session_id)
        except StripeError:
            current = None
        if current and current.get("payment_status") == "paid":
            apply_checkout_session(current)
            raise ValidationError({"detail": "You already have this game in your library."})
        if (
            current
            and current.get("status") == "open"
            and current.get("url")
            and current.get("amount_total") in (None, purchase.price_cents)
        ):
            return purchase, current
        try:
            cancel_checkout(purchase.stripe_checkout_session_id)
        except StripeError:
            pass

    success_url, cancel_url = _checkout_urls()
    params = game_checkout_params(
        purchase,
        listing,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_id=_customer_id(user),
    )
    try:
        session = start_checkout(params)
    except StripeError:
        purchase.status = Purchase.Status.FAILED
        purchase.save(update_fields=["status", "updated_at"])
        raise
    session_id = session.get("id") or ""
    checkout_url = session.get("url") or ""
    if not session_id or not checkout_url:
        purchase.status = Purchase.Status.FAILED
        purchase.save(update_fields=["status", "updated_at"])
        raise StripeError("Stripe did not return a Checkout URL.")
    purchase.stripe_checkout_session_id = session_id
    purchase.save(update_fields=["stripe_checkout_session_id", "updated_at"])
    return purchase, session


def apply_checkout_session(session: dict, *, event_type: str = "") -> Purchase | None:
    session_id = session.get("id") or ""
    purchase = None
    if session_id:
        purchase = Purchase.objects.filter(stripe_checkout_session_id=session_id).first()
    if purchase is None:
        metadata = session.get("metadata") or {}
        raw_id = metadata.get("purchase_id") or session.get("client_reference_id")
        if raw_id:
            purchase = Purchase.objects.filter(pk=raw_id).first()
    if purchase is None:
        return None

    paid = session.get("payment_status") == "paid" or event_type == "checkout.session.async_payment_succeeded"
    failed = event_type in {
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    } or session.get("status") == "expired"

    with transaction.atomic():
        purchase = _lock(Purchase.objects.all()).get(pk=purchase.pk)
        if paid:
            purchase = mark_purchase_paid(purchase, session)
        elif failed and purchase.status == Purchase.Status.PENDING:
            purchase.status = (
                Purchase.Status.FAILED
                if event_type == "checkout.session.async_payment_failed"
                else Purchase.Status.CANCELED
            )
            _capture_ids(purchase, session)
            purchase.save()
        else:
            _capture_ids(purchase, session)
            purchase.save()

    if purchase.status == Purchase.Status.PAID:
        try:
            attempt_payout(purchase.seller)
        except StripeError:
            pass
    return purchase


def confirm_checkout(user, session_id: str) -> Purchase:
    session = fetch_checkout(session_id)
    purchase = apply_checkout_session(session)
    if purchase is None or purchase.buyer_id != user.id:
        raise ValidationError({"session_id": "No purchase found for this Checkout Session."})
    return purchase


def sync_earning_holds(creator_id: int) -> None:
    now = timezone.now()
    Earning.objects.filter(
        creator_id=creator_id,
        status=Earning.Status.HELD,
        available_at__lte=now,
        unpaid_cents__gt=0,
    ).update(status=Earning.Status.AVAILABLE)


def _sum(creator_id: int, **filters) -> int:
    total = Earning.objects.filter(creator_id=creator_id, **filters).aggregate(total=Sum("unpaid_cents"))
    return int(total["total"] or 0)


def balance_for(creator) -> dict:
    sync_earning_holds(creator.pk)
    held = _sum(creator.pk, status=Earning.Status.HELD)
    available = _sum(creator.pk, status=Earning.Status.AVAILABLE)
    paid = Earning.objects.filter(creator=creator).aggregate(total=Sum("settled_cents"))
    return {
        "held_cents": held,
        "available_cents": available,
        "paid_out_cents": int(paid["total"] or 0),
        "min_payout_cents": min_payout_cents(),
        "hold_days": hold_days(),
    }


def refresh_connected_account(account: ConnectedAccount) -> ConnectedAccount:
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return account
    payload = retrieve_connected_account(account.stripe_account_id)
    account.transfers_status = capability_status(payload, "stripe_transfers")
    account.payouts_status = capability_status(payload, "payouts")
    account.save(update_fields=["transfers_status", "payouts_status", "updated_at"])
    return account


def ensure_connected_account(user) -> ConnectedAccount:
    existing = ConnectedAccount.objects.filter(user=user).first()
    if existing:
        return refresh_connected_account(existing)
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise StripeError("Stripe is not configured.", status_code=503)
    payload = create_connected_account(user)
    account_id = payload.get("id") or ""
    if not account_id:
        raise StripeError("Stripe did not return a connected account.")
    account, _created = ConnectedAccount.objects.get_or_create(
        user=user,
        defaults={
            "stripe_account_id": account_id,
            "transfers_status": capability_status(payload, "stripe_transfers"),
            "payouts_status": capability_status(payload, "payouts"),
        },
    )
    if account.stripe_account_id != account_id and not _created:
        return refresh_connected_account(account)
    return account


def _payout_block_reason(creator, account: ConnectedAccount | None, balance: dict) -> str:
    # A fresh card payment cannot be paid out during the hold, even after setup.
    if balance["available_cents"] <= 0 and balance["held_cents"] > 0:
        return REASON_HOLD
    if account is None:
        return REASON_SETUP
    if account.transfers_status != "active":
        return REASON_TRANSFERS
    if account.payouts_status != "active":
        return REASON_PAYOUTS
    if balance["available_cents"] < min_payout_cents():
        if balance["available_cents"] <= 0:
            return REASON_NONE
        return REASON_MINIMUM
    return ""


def _payout_key(earning_ids: list[int], total: int) -> str:
    raw = ",".join(str(value) for value in sorted(earning_ids)) + f":{total}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"mkt-payout-{digest}"


def attempt_payout(creator) -> dict:
    """Transfer cleared earnings once they reach $20 and transfers are active."""
    sync_earning_holds(creator.pk)
    account = ConnectedAccount.objects.filter(user=creator).first()
    if account is not None and getattr(settings, "STRIPE_SECRET_KEY", ""):
        try:
            account = refresh_connected_account(account)
        except StripeError as exc:
            balance = balance_for(creator)
            return {
                "transferred": False,
                "amount_cents": 0,
                "payout_id": None,
                "blocked_reason": str(exc),
                "balance": balance,
            }
    balance = balance_for(creator)
    reason = _payout_block_reason(creator, account, balance)
    if reason:
        return {
            "transferred": False,
            "amount_cents": 0,
            "payout_id": None,
            "blocked_reason": reason,
            "balance": balance,
        }

    assert account is not None
    with transaction.atomic():
        earnings = list(
            _lock(
                Earning.objects.filter(
                    creator=creator,
                    status=Earning.Status.AVAILABLE,
                    unpaid_cents__gt=0,
                )
            ).order_by("available_at", "id")
        )
        total = sum(earning.unpaid_cents for earning in earnings)
        if total < min_payout_cents():
            balance = balance_for(creator)
            held_reason = REASON_HOLD if balance["held_cents"] else REASON_MINIMUM
            if total <= 0 and balance["held_cents"]:
                held_reason = REASON_HOLD
            elif total <= 0:
                held_reason = REASON_NONE
            else:
                held_reason = REASON_MINIMUM
            return {
                "transferred": False,
                "amount_cents": 0,
                "payout_id": None,
                "blocked_reason": held_reason,
                "balance": balance,
            }
        # Recheck the account row inside the lock. Capability status came from v2.
        account = _lock(ConnectedAccount.objects.all()).get(pk=account.pk)
        if account.transfers_status != "active" or account.payouts_status != "active":
            balance = balance_for(creator)
            return {
                "transferred": False,
                "amount_cents": 0,
                "payout_id": None,
                "blocked_reason": REASON_TRANSFERS
                if account.transfers_status != "active"
                else REASON_PAYOUTS,
                "balance": balance,
            }
        key = _payout_key([earning.pk for earning in earnings], total)
        currency = getattr(settings, "MARKETPLACE_CURRENCY", "usd")
        transfer = send_transfer(
            amount=total,
            currency=currency,
            destination=account.stripe_account_id,
            user_id=creator.pk,
            idempotency_key=key,
        )
        transfer_id = transfer.get("id") or ""
        if not transfer_id:
            raise StripeError("Stripe did not return a transfer.")
        payout, created = Payout.objects.get_or_create(
            stripe_transfer_id=transfer_id,
            defaults={
                "creator": creator,
                "amount_cents": total,
                "currency": currency,
                "status": Payout.Status.PAID,
                "idempotency_key": key,
            },
        )
        if created:
            for earning in earnings:
                earning.settled_cents += earning.unpaid_cents
                earning.unpaid_cents = 0
                earning.status = Earning.Status.PAID
                earning.payout = payout
                earning.save()

    balance = balance_for(creator)
    return {
        "transferred": True,
        "amount_cents": payout.amount_cents,
        "payout_id": payout.pk,
        "blocked_reason": "",
        "balance": balance,
    }


def _refresh_earning_status(earning: Earning) -> None:
    now = timezone.now()
    if earning.unpaid_cents <= 0 and earning.settled_cents <= 0:
        earning.status = Earning.Status.REVERSED
    elif earning.unpaid_cents <= 0:
        earning.status = Earning.Status.PAID
    elif earning.available_at <= now:
        earning.status = Earning.Status.AVAILABLE
    else:
        earning.status = Earning.Status.HELD


def _claw_back(earning: Earning, amount: int) -> None:
    if amount <= 0:
        return
    from_unpaid = min(earning.unpaid_cents, amount)
    earning.unpaid_cents -= from_unpaid
    rest = amount - from_unpaid
    if rest > 0:
        take = min(earning.settled_cents, rest)
        payout = earning.payout
        if take <= 0 or payout is None or not payout.stripe_transfer_id:
            raise StripeError("Paid earnings have no transfer to reverse.", status_code=409)
        reverse_transfer(
            transfer_id=payout.stripe_transfer_id,
            amount=take,
            purchase_id=earning.purchase_id,
            idempotency_key=f"mkt-rev-{earning.purchase_id}-{earning.reversed_cents + from_unpaid + take}",
        )
        earning.settled_cents -= take
        payout.reversed_cents += take
        payout.save(update_fields=["reversed_cents", "updated_at"])
        rest -= take
        if rest > 0:
            raise StripeError("Could not recover the full creator credit.", status_code=409)
    earning.reversed_cents = earning.credit_cents - earning.unpaid_cents - earning.settled_cents
    _refresh_earning_status(earning)
    earning.save()


def _restore_earning(earning: Earning, amount: int) -> None:
    amount = min(amount, earning.reversed_cents)
    if amount <= 0:
        return
    earning.reversed_cents -= amount
    earning.unpaid_cents += amount
    _refresh_earning_status(earning)
    earning.save()


def apply_money_adjustment(
    purchase: Purchase,
    *,
    refunded_cents: int | None = None,
    disputed_cents: int | None = None,
) -> Purchase:
    """Shrink unpaid earnings, or reverse a transfer Chili already sent."""
    if purchase.price_cents <= 0:
        return purchase
    try:
        earning = purchase.earning
    except Earning.DoesNotExist:
        return purchase
    if refunded_cents is None:
        refunded_cents = purchase.refunded_cents
    if disputed_cents is None:
        disputed_cents = purchase.disputed_cents
    refunded_cents = max(0, min(int(refunded_cents), purchase.price_cents))
    disputed_cents = max(0, min(int(disputed_cents), purchase.price_cents))
    basis = max(refunded_cents, disputed_cents)
    target_reversed = proportional(earning.credit_cents, basis, purchase.price_cents)
    current_reversed = earning.credit_cents - earning.unpaid_cents - earning.settled_cents
    delta = target_reversed - current_reversed
    with transaction.atomic():
        earning = _lock(Earning.objects.all()).get(pk=earning.pk)
        purchase = _lock(Purchase.objects.all()).get(pk=purchase.pk)
        current_reversed = earning.credit_cents - earning.unpaid_cents - earning.settled_cents
        delta = target_reversed - current_reversed
        if delta > 0:
            _claw_back(earning, delta)
        elif delta < 0:
            _restore_earning(earning, -delta)
        purchase.refunded_cents = refunded_cents
        purchase.disputed_cents = disputed_cents
        if purchase.status in {Purchase.Status.PAID, Purchase.Status.REFUNDED, Purchase.Status.DISPUTED}:
            if disputed_cents >= purchase.price_cents and disputed_cents >= refunded_cents:
                purchase.status = Purchase.Status.DISPUTED
            elif refunded_cents >= purchase.price_cents:
                purchase.status = Purchase.Status.REFUNDED
            else:
                purchase.status = Purchase.Status.PAID
        purchase.save()
    return purchase


def refund_purchase(purchase: Purchase) -> Purchase:
    if purchase.status != Purchase.Status.PAID or purchase.price_cents <= 0:
        raise ValidationError({"detail": "Only a paid game can be refunded."})
    if not purchase.stripe_payment_intent_id:
        raise ValidationError({"detail": "This purchase has no card charge to refund."})
    refund_payment_intent(purchase.stripe_payment_intent_id, purchase_id=purchase.pk)
    return apply_money_adjustment(purchase, refunded_cents=purchase.price_cents)


def find_purchase_for_stripe_object(obj: dict) -> Purchase | None:
    payment_intent = obj.get("payment_intent")
    if isinstance(payment_intent, dict):
        payment_intent = payment_intent.get("id")
    if payment_intent:
        found = Purchase.objects.filter(stripe_payment_intent_id=payment_intent).first()
        if found:
            return found
    charge_id = ""
    if obj.get("object") == "charge":
        charge_id = obj.get("id") or ""
    elif obj.get("charge"):
        charge_id = str(obj.get("charge"))
    if charge_id:
        found = Purchase.objects.filter(stripe_charge_id=charge_id).first()
        if found:
            return found
    metadata = obj.get("metadata") or {}
    raw_id = metadata.get("purchase_id")
    if raw_id:
        return Purchase.objects.filter(pk=raw_id).first()
    return None


def handle_marketplace_event(event: dict) -> bool:
    """Return True when this event belongs to a game sale. Kit checkout is untouched."""
    event_type = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}
    if not isinstance(obj, dict):
        return False

    if event_type in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }:
        metadata = obj.get("metadata") or {}
        session_id = obj.get("id") or ""
        known = metadata.get("kind") == "marketplace_purchase" or (
            session_id and Purchase.objects.filter(stripe_checkout_session_id=session_id).exists()
        )
        raw_id = metadata.get("purchase_id")
        if not known and raw_id:
            known = Purchase.objects.filter(pk=raw_id).exists()
        if not known:
            return False
        apply_checkout_session(obj, event_type=event_type)
        return True

    if event_type == "charge.refunded":
        purchase = find_purchase_for_stripe_object(obj)
        if purchase is None:
            return False
        if obj.get("id") and not purchase.stripe_charge_id:
            purchase.stripe_charge_id = obj["id"]
            purchase.save(update_fields=["stripe_charge_id", "updated_at"])
        apply_money_adjustment(purchase, refunded_cents=int(obj.get("amount_refunded") or 0))
        return True

    if event_type.startswith("charge.dispute"):
        purchase = find_purchase_for_stripe_object(obj)
        if purchase is None:
            return False
        status = obj.get("status") or ""
        if status == "won":
            apply_money_adjustment(purchase, disputed_cents=0)
        else:
            amount = int(obj.get("amount") or purchase.price_cents)
            apply_money_adjustment(purchase, disputed_cents=amount)
        return True

    if "account" in event_type:
        account_id = str(obj.get("id") or "")
        if not account_id.startswith("acct_"):
            return False
        account = ConnectedAccount.objects.filter(stripe_account_id=account_id).first()
        if account is None:
            return False
        try:
            refresh_connected_account(account)
            attempt_payout(account.user)
        except StripeError:
            pass
        return True

    return False


def owned_game_ids(user) -> set[int]:
    if not user or not user.is_authenticated:
        return set()
    return set(
        Purchase.objects.filter(
            buyer=user,
            status=Purchase.Status.PAID,
            game_id__isnull=False,
        ).values_list("game_id", flat=True)
    )


def library_game_ids(user) -> set[int]:
    """Games the viewer can play: a library copy, or a game they released."""
    if not user or not user.is_authenticated:
        return set()
    bought = set(
        Purchase.objects.filter(
            buyer=user,
            status__in=(
                Purchase.Status.PAID,
                Purchase.Status.REFUNDED,
                Purchase.Status.DISPUTED,
            ),
            game_id__isnull=False,
        ).values_list("game_id", flat=True)
    )
    released = set(Game.objects.filter(owner=user, released=True).values_list("id", flat=True))
    return bought | released


def viewer_ratings(user) -> dict[int, int]:
    if not user or not user.is_authenticated:
        return {}
    return dict(Rating.objects.filter(user=user).values_list("game_id", "stars"))


def submit_rating(user, listing: Listing, stars: int) -> Rating:
    """One rating per user per game. Library membership is required, and it is not editable."""
    if listing.game_id not in library_game_ids(user):
        raise PermissionDenied("You can rate a game only when it is in your library.")
    if Rating.objects.filter(user=user, game_id=listing.game_id).exists():
        raise ValidationError({"detail": "You already rated this game."})
    try:
        with transaction.atomic():
            return Rating.objects.create(user=user, game_id=listing.game_id, stars=stars)
    except IntegrityError as exc:
        if Rating.objects.filter(user=user, game_id=listing.game_id).exists():
            raise ValidationError({"detail": "You already rated this game."}) from exc
        raise


def listing_queryset():
    average = (
        Rating.objects.filter(game_id=OuterRef("game_id"))
        .order_by()
        .values("game_id")
        .annotate(value=Avg("stars"))
        .values("value")
    )
    total = (
        Rating.objects.filter(game_id=OuterRef("game_id"))
        .order_by()
        .values("game_id")
        .annotate(value=Count("id"))
        .values("value")
    )
    return (
        Listing.objects.select_related("game", "seller", "category")
        .prefetch_related("tags")
        .annotate(
            rating_average=Subquery(average, output_field=FloatField()),
            rating_count=Coalesce(Subquery(total, output_field=IntegerField()), Value(0)),
        )
    )


def public_listings(params):
    queryset = listing_queryset().filter(published=True)
    query = (params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(game__title__icontains=query)
            | Q(description__icontains=query)
            | Q(seller__username__icontains=query)
            | Q(tags__name__icontains=query)
        ).distinct()
    category = (params.get("category") or "").strip()
    if category:
        queryset = queryset.filter(category__slug=category)
    tag = (params.get("tag") or "").strip().lower()
    if tag:
        queryset = queryset.filter(tags__name=tag)
    price = (params.get("price") or "").strip().lower()
    if price == "free":
        queryset = queryset.filter(price_cents=0)
    elif price == "paid":
        queryset = queryset.filter(price_cents__gt=0)
    sort = (params.get("sort") or "new").strip().lower()
    if sort == "price_asc":
        queryset = queryset.order_by("price_cents", "-id")
    elif sort == "price_desc":
        queryset = queryset.order_by("-price_cents", "-id")
    elif sort == "title":
        queryset = queryset.order_by("game__title", "-id")
    else:
        queryset = queryset.order_by("-created_at", "-id")
    return queryset


def popular_tags(limit: int = 40) -> list[dict]:
    rows = (
        ListingTag.objects.filter(listing__published=True)
        .values("name")
        .annotate(count=Count("id"))
        .order_by("-count", "name")[:limit]
    )
    return [{"name": row["name"], "count": row["count"]} for row in rows]


def account_is_ready(account: ConnectedAccount | None) -> bool:
    if account is None:
        return False
    payload = {
        "configuration": {
            "recipient": {
                "capabilities": {
                    "stripe_balance": {
                        "stripe_transfers": {"status": account.transfers_status},
                        "payouts": {"status": account.payouts_status},
                    }
                }
            }
        }
    }
    return transfers_active(payload) and payouts_active(payload)
