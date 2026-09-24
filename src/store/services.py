from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from store.models import Order, OrderItem, Product, product_image_urls
from store.stripe import StripeError, create_checkout_session, retrieve_checkout_session
from store.sync import (
    ensure_stripe_customer,
    order_stripe_metadata,
    stripe_sync_enabled,
    sync_order_to_stripe,
    sync_product_to_stripe,
)


class InsufficientStock(ValidationError):
    default_detail = "Not enough stock for one or more products."


def _shipping_countries() -> list[str]:
    from django.conf import settings

    return list(getattr(settings, "STORE_SHIPPING_COUNTRIES", ["US"]))


def reserve_stock(product: Product, quantity: int) -> None:
    updated = Product.objects.filter(pk=product.pk, stock__gte=quantity).update(
        stock=F("stock") - quantity
    )
    if updated != 1:
        raise InsufficientStock(
            {"items": [f'"{product.name}" does not have {quantity} in stock.']}
        )


def release_stock(order: Order) -> None:
    for item in order.items.all():
        Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)


def _shipping_blob(session: dict) -> dict:
    collected = session.get("collected_information") or {}
    details = collected.get("shipping_details") or session.get("shipping_details") or {}
    return details if isinstance(details, dict) else {}


def _apply_shipping(order: Order, session: dict) -> None:
    details = _shipping_blob(session)
    address = details.get("address") or {}
    customer = session.get("customer_details") or {}
    customer_address = customer.get("address") or {}
    if not isinstance(address, dict):
        address = {}
    if not isinstance(customer_address, dict):
        customer_address = {}
    order.shipping_name = details.get("name") or customer.get("name") or order.shipping_name or ""
    order.shipping_line1 = address.get("line1") or customer_address.get("line1") or ""
    order.shipping_line2 = address.get("line2") or customer_address.get("line2") or ""
    order.shipping_city = address.get("city") or customer_address.get("city") or ""
    order.shipping_state = address.get("state") or customer_address.get("state") or ""
    order.shipping_postal_code = (
        address.get("postal_code") or customer_address.get("postal_code") or ""
    )
    country = address.get("country") or customer_address.get("country") or ""
    order.shipping_country = str(country).upper()
    order.customer_email = customer.get("email") or order.customer_email
    payment_intent = session.get("payment_intent")
    if isinstance(payment_intent, dict):
        order.stripe_payment_intent_id = payment_intent.get("id") or ""
    elif payment_intent:
        order.stripe_payment_intent_id = str(payment_intent)


def hydrate_shipping(order: Order) -> Order:
    """Pull address from Stripe when an older session sync only stored the name."""
    if order.shipping_line1 or not order.stripe_checkout_session_id:
        return order
    if order.status not in {Order.Status.PAID, Order.Status.FULFILLED}:
        return order
    try:
        session = retrieve_checkout_session(order.stripe_checkout_session_id)
    except StripeError:
        return order
    _apply_shipping(order, session)
    order.save()
    return order


def mark_paid(order: Order, session: dict) -> Order:
    if order.status in {Order.Status.PAID, Order.Status.FULFILLED}:
        _apply_shipping(order, session)
        order.save()
        return order
    if order.status != Order.Status.PENDING:
        return order
    _apply_shipping(order, session)
    order.status = Order.Status.PAID
    if order.shipping_status == Order.ShippingStatus.AWAITING_PAYMENT:
        order.shipping_status = Order.ShippingStatus.PREPARING
    order.paid_at = timezone.now()
    order.save()
    sync_order_to_stripe(order)
    return order


def mark_canceled(order: Order, *, status: str = Order.Status.CANCELED) -> Order:
    if order.status != Order.Status.PENDING:
        return order
    release_stock(order)
    order.status = status
    order.shipping_status = Order.ShippingStatus.NOT_SHIPPING
    order.save()
    sync_order_to_stripe(order)
    return order


def _locked_order(pk: int) -> Order:
    queryset = Order.objects.all()
    # D1 rejects SELECT FOR UPDATE even though Django's SQLite flags claim support.
    if not getattr(settings, "ON_WORKERS", False):
        queryset = queryset.select_for_update()
    return queryset.get(pk=pk)


def _fail_checkout_order(order: Order) -> None:
    try:
        with transaction.atomic():
            locked = _locked_order(order.pk)
            mark_canceled(locked, status=Order.Status.FAILED)
    except Exception:
        pending = Order.objects.filter(pk=order.pk).first()
        if pending is not None:
            mark_canceled(pending, status=Order.Status.FAILED)


def apply_checkout_session(session: dict) -> Order | None:
    session_id = session.get("id") or ""
    order = None
    if session_id:
        order = Order.objects.filter(stripe_checkout_session_id=session_id).first()
    if order is None:
        metadata = session.get("metadata") or {}
        raw_id = metadata.get("order_id") or session.get("client_reference_id")
        if raw_id:
            order = Order.objects.filter(pk=raw_id).first()
            if order and session_id and not order.stripe_checkout_session_id:
                order.stripe_checkout_session_id = session_id
                order.save(update_fields=["stripe_checkout_session_id", "updated_at"])
    if order is None:
        return None

    with transaction.atomic():
        order = _locked_order(order.pk)
        event_type = session.get("type") or ""
        stripe_status = session.get("status")
        payment_status = session.get("payment_status")
        if payment_status == "paid":
            return mark_paid(order, session)
        if event_type == "checkout.session.async_payment_failed":
            return mark_canceled(order, status=Order.Status.FAILED)
        if event_type == "checkout.session.expired" or stripe_status == "expired":
            return mark_canceled(order)
        _apply_shipping(order, session)
        order.save()
        return order


def handle_stripe_event(event: dict) -> Order | None:
    event_type = event.get("type") or ""
    session = event.get("data", {}).get("object") or {}
    if not isinstance(session, dict):
        return None
    session = {**session, "type": event_type}
    if event_type in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }:
        return apply_checkout_session(session)
    return None


def sync_checkout_session(session_id: str, user) -> Order:
    session = retrieve_checkout_session(session_id)
    order = apply_checkout_session(session)
    if order is None or order.user_id != user.id:
        raise ValidationError({"session_id": "No order found for this Checkout Session."})
    return order


def create_order_checkout(user, items: list[dict]) -> tuple[Order, dict]:
    from django.conf import settings

    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        raise StripeError("Stripe is not configured.", status_code=503)

    currency = getattr(settings, "STORE_CURRENCY", "usd")
    line_items = []
    prepared: list[tuple[Product, int]] = []

    for item in items:
        product: Product = item["product"]
        quantity: int = item["quantity"]
        if not product.is_active:
            raise ValidationError({"items": [f'"{product.name}" is not available.']})
        if product.currency.lower() != currency:
            raise ValidationError(
                {"items": [f'"{product.name}" must be purchased in {product.currency}.']}
            )
        if product.stock < quantity:
            raise InsufficientStock(
                {"items": [f'"{product.name}" does not have {quantity} in stock.']}
            )
        prepared.append((product, quantity))
        if not product.stripe_price_id and stripe_sync_enabled():
            try:
                sync_product_to_stripe(product)
            except StripeError:
                pass
        if product.stripe_price_id:
            line_items.append({"price": product.stripe_price_id, "quantity": quantity})
        else:
            product_data = {"name": product.name}
            if product.short_description:
                product_data["description"] = product.short_description[:500]
            images = product_image_urls(product)
            if images:
                product_data["images"] = images[:8]
            product_data["metadata"] = {"product_id": str(product.id)}
            line_items.append(
                {
                    "quantity": quantity,
                    "price_data": {
                        "currency": currency,
                        "unit_amount": product.price_cents,
                        "product_data": product_data,
                    },
                }
            )

    with transaction.atomic():
        order = Order.objects.create(
            user=user,
            currency=currency,
            customer_email=user.email or "",
            shipping_status=Order.ShippingStatus.AWAITING_PAYMENT,
        )
        total = 0
        for product, quantity in prepared:
            reserve_stock(product, quantity)
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                unit_price_cents=product.price_cents,
                quantity=quantity,
            )
            total += product.price_cents * quantity
        order.total_cents = total
        order.save(update_fields=["total_cents", "updated_at"])

    success_url = getattr(settings, "STORE_CHECKOUT_SUCCESS_URL")
    cancel_url = getattr(settings, "STORE_CHECKOUT_CANCEL_URL")
    customer_id = None
    if stripe_sync_enabled():
        try:
            customer_id = ensure_stripe_customer(user)
        except StripeError:
            customer_id = None
    if customer_id:
        order.stripe_customer_id = customer_id
        order.save(update_fields=["stripe_customer_id", "updated_at"])

    params = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(order.id),
        "integration_identifier": getattr(
            settings, "STORE_INTEGRATION_IDENTIFIER", "chili-store-hwchkout"
        ),
        "line_items": line_items,
        "metadata": order_stripe_metadata(order),
        "payment_intent_data": {
            "description": f"Chili Platform order #{order.id}",
            "metadata": order_stripe_metadata(order),
        },
        "invoice_creation": {"enabled": True},
        "shipping_address_collection": {
            "allowed_countries": _shipping_countries(),
        },
    }
    if customer_id:
        params["customer"] = customer_id
    elif user.email:
        params["customer_email"] = user.email

    try:
        session = create_checkout_session(params)
    except StripeError:
        params.pop("invoice_creation", None)
        try:
            session = create_checkout_session(params)
        except StripeError:
            _fail_checkout_order(order)
            raise

    checkout_url = session.get("url")
    session_id = session.get("id")
    if not checkout_url or not session_id:
        _fail_checkout_order(order)
        raise StripeError("Stripe did not return a Checkout URL.")

    order.stripe_checkout_session_id = session_id
    order.save(update_fields=["stripe_checkout_session_id", "updated_at"])
    return order, session
