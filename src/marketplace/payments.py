"""Stripe calls for game sales.

Card charges stay on Chili's account. Creator payouts are a later transfer.
Destination charges and ``application_fee_amount`` are refused.
"""

from __future__ import annotations

import urllib.parse

from django.conf import settings

from store.stripe import (
    StripeError,
    create_account_session,
    create_checkout_session,
    create_refund,
    create_transfer,
    create_transfer_reversal,
    expire_checkout_session,
    request_json,
    retrieve_checkout_session,
)

CHARGE_FORBIDDEN_KEYS = frozenset(
    {"application_fee_amount", "transfer_data", "on_behalf_of", "destination"}
)
EMBEDDED_COMPONENTS = (
    "account_onboarding",
    "notification_banner",
    "account_management",
    "payouts",
)


def _walk_forbidden(value, forbidden: frozenset[str], trail: str = "") -> str:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in forbidden:
                return f"{trail}.{key}" if trail else key
            found = _walk_forbidden(nested, forbidden, f"{trail}.{key}" if trail else key)
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _walk_forbidden(nested, forbidden, f"{trail}[{index}]")
            if found:
                return found
    return ""


def assert_platform_charge(params: dict) -> None:
    found = _walk_forbidden(params, CHARGE_FORBIDDEN_KEYS)
    if found:
        raise StripeError(
            f"Game charges stay on Chili's account and cannot include {found}.",
            status_code=500,
        )


def recipient_account_body(user) -> dict:
    """Accounts v2 recipient. No dashboard, no merchant card payments."""
    return {
        "dashboard": "none",
        "contact_email": user.email,
        "display_name": user.get_username(),
        "identity": {
            "country": "us",
            "entity_type": "individual",
        },
        "configuration": {
            "recipient": {
                "capabilities": {
                    "stripe_balance": {
                        "stripe_transfers": {"requested": True},
                    }
                }
            }
        },
        "defaults": {
            "responsibilities": {
                "fees_collector": "application",
                "losses_collector": "application",
            }
        },
        "include": ["configuration.recipient"],
        "metadata": {
            "user_id": str(user.pk),
            "username": user.get_username(),
        },
    }


def assert_recipient_account(body: dict) -> None:
    if body.get("dashboard") != "none":
        raise StripeError("Creators do not get a Stripe dashboard.", status_code=500)
    if "type" in body:
        raise StripeError("Legacy account types are not used.", status_code=500)
    config = body.get("configuration") or {}
    if "merchant" in config or "customer" in config:
        raise StripeError("Marketplace accounts are recipients, not merchants.", status_code=500)
    balance = (
        ((config.get("recipient") or {}).get("capabilities") or {}).get("stripe_balance") or {}
    )
    if "payouts" in balance:
        raise StripeError(
            "Do not request stripe_balance.payouts; stripe_transfers requests it.",
            status_code=500,
        )
    transfers = balance.get("stripe_transfers") or {}
    if transfers.get("requested") is not True:
        raise StripeError("Recipient accounts must request stripe_transfers.", status_code=500)
    responsibilities = ((body.get("defaults") or {}).get("responsibilities") or {})
    if responsibilities.get("fees_collector") != "application":
        raise StripeError("Chili manages pricing on game sales.", status_code=500)
    if responsibilities.get("losses_collector") != "application":
        raise StripeError("Chili is liable for creator negative balances.", status_code=500)
    if _walk_forbidden(body, frozenset({"application_fee_amount", "card_payments"})):
        raise StripeError("Marketplace accounts do not take card payments.", status_code=500)


def capability_status(account: dict, name: str) -> str:
    """Read a v2 recipient capability. Do not use charges_enabled or payouts_enabled."""
    node = account
    for key in (
        "configuration",
        "recipient",
        "capabilities",
        "stripe_balance",
        name,
        "status",
    ):
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return str(node or "")


def transfers_active(account: dict) -> bool:
    return capability_status(account, "stripe_transfers") == "active"


def payouts_active(account: dict) -> bool:
    return capability_status(account, "payouts") == "active"


def create_connected_account(user) -> dict:
    body = recipient_account_body(user)
    assert_recipient_account(body)
    return request_json(
        "POST",
        "/v2/core/accounts",
        body,
        idempotency_key=f"mkt-account-{user.pk}",
    )


def retrieve_connected_account(account_id: str) -> dict:
    quoted = urllib.parse.quote(account_id, safe="")
    query = urllib.parse.urlencode([("include", "configuration.recipient")])
    return request_json("GET", f"/v2/core/accounts/{quoted}?{query}")


def account_session_params(account_id: str) -> dict:
    # requirement collection is the platform's when dashboard is none and Chili
    # owns losses, so creators authenticate with Chili, not a Stripe login.
    features = {"disable_stripe_user_authentication": True}
    return {
        "account": account_id,
        "components": {
            name: {"enabled": True, "features": features} for name in EMBEDDED_COMPONENTS
        },
    }


def open_account_session(account_id: str) -> dict:
    params = account_session_params(account_id)
    return create_account_session(params)


def game_checkout_params(purchase, listing, *, success_url: str, cancel_url: str, customer_id: str) -> dict:
    game = listing.game
    description = (listing.description or game.title)[:500]
    metadata = {
        "kind": "marketplace_purchase",
        "purchase_id": str(purchase.pk),
    }
    params = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(purchase.pk),
        "integration_identifier": getattr(
            settings,
            "MARKETPLACE_INTEGRATION_IDENTIFIER",
            "chili-mkt-kprwqmzn",
        ),
        "line_items": [
            {
                "quantity": 1,
                "price_data": {
                    "currency": purchase.currency,
                    "unit_amount": purchase.price_cents,
                    "product_data": {
                        "name": game.title[:120],
                        "description": description,
                        "metadata": {
                            "listing_id": str(listing.pk),
                            "game_id": str(game.pk),
                        },
                    },
                },
            }
        ],
        "metadata": metadata,
        "payment_intent_data": {
            "description": f"Chili Platform game: {game.title}"[:1000],
            "metadata": metadata,
        },
    }
    if customer_id:
        params["customer"] = customer_id
    elif purchase.buyer.email:
        params["customer_email"] = purchase.buyer.email
    assert_platform_charge(params)
    return params


def start_checkout(params: dict) -> dict:
    assert_platform_charge(params)
    return create_checkout_session(params)


def fetch_checkout(session_id: str) -> dict:
    return retrieve_checkout_session(session_id)


def cancel_checkout(session_id: str) -> dict:
    return expire_checkout_session(session_id)


def send_transfer(*, amount: int, currency: str, destination: str, user_id: int, idempotency_key: str) -> dict:
    if amount <= 0:
        raise StripeError("Refusing a zero transfer.", status_code=400)
    params = {
        "amount": amount,
        "currency": currency,
        "destination": destination,
        "metadata": {
            "kind": "marketplace_payout",
            "user_id": str(user_id),
        },
    }
    return create_transfer(params, idempotency_key=idempotency_key)


def reverse_transfer(*, transfer_id: str, amount: int, purchase_id: int, idempotency_key: str) -> dict:
    if amount <= 0:
        raise StripeError("Refusing a zero reversal.", status_code=400)
    return create_transfer_reversal(
        transfer_id,
        {
            "amount": amount,
            "metadata": {
                "kind": "marketplace_reversal",
                "purchase_id": str(purchase_id),
            },
        },
        idempotency_key=idempotency_key,
    )


def refund_payment_intent(payment_intent_id: str, *, purchase_id: int) -> dict:
    return create_refund(
        {"payment_intent": payment_intent_id},
        idempotency_key=f"mkt-refund-{purchase_id}",
    )
