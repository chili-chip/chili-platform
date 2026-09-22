"""Push catalog products, customers, and order metadata to Stripe."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model

from store.models import Order, Product, product_image_urls
from store.stripe import (
    StripeError,
    create_customer,
    create_price,
    create_product,
    list_customers,
    retrieve_price,
    update_customer,
    update_payment_intent,
    update_price,
    update_product,
)


def stripe_sync_enabled() -> bool:
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return False
    return bool(getattr(settings, "STRIPE_SYNC_ENABLED", True))


def sync_product_to_stripe(product: Product, request=None) -> Product:
    if not stripe_sync_enabled():
        return product

    payload: dict = {
        "name": product.name,
        "active": product.is_active,
        "metadata": {
            "product_id": str(product.pk),
            "sku": product.sku,
            "slug": product.slug,
        },
    }
    if product.short_description:
        payload["description"] = product.short_description[:1000]
    images = product_image_urls(product, request=request)
    if images:
        payload["images"] = images

    if product.stripe_product_id:
        stripe_product = update_product(product.stripe_product_id, payload)
    else:
        stripe_product = create_product(payload)

    product_id = stripe_product["id"]
    price_id = product.stripe_price_id
    reuse_price = False
    if price_id:
        try:
            existing = retrieve_price(price_id)
        except StripeError:
            existing = {}
        reuse_price = (
            existing.get("unit_amount") == product.price_cents
            and (existing.get("currency") or "").lower() == product.currency.lower()
            and existing.get("product") == product_id
        )
        if reuse_price and existing.get("active") != product.is_active:
            update_price(price_id, {"active": product.is_active})

    if not reuse_price:
        if price_id:
            try:
                update_price(price_id, {"active": False})
            except StripeError:
                pass
        created = create_price(
            {
                "product": product_id,
                "currency": product.currency,
                "unit_amount": product.price_cents,
                "metadata": {"product_id": str(product.pk)},
            }
        )
        price_id = created["id"]

    Product.objects.filter(pk=product.pk).update(
        stripe_product_id=product_id,
        stripe_price_id=price_id,
    )
    product.stripe_product_id = product_id
    product.stripe_price_id = price_id
    return product


def ensure_stripe_customer(user) -> str | None:
    if not stripe_sync_enabled() or not user.email:
        return None
    if user.stripe_customer_id:
        return user.stripe_customer_id

    listed = list_customers(email=user.email, limit=1)
    data = listed.get("data") if isinstance(listed, dict) else None
    if isinstance(data, list) and data:
        customer_id = data[0].get("id") or ""
    else:
        customer_id = create_customer(
            {
                "email": user.email,
                "name": user.get_username(),
                "metadata": {"user_id": str(user.pk), "username": user.get_username()},
            }
        ).get("id") or ""
    if not customer_id:
        return None
    get_user_model().objects.filter(pk=user.pk).update(stripe_customer_id=customer_id)
    user.stripe_customer_id = customer_id
    return customer_id


def order_stripe_metadata(order: Order) -> dict[str, str]:
    return {
        "order_id": str(order.pk),
        "order_status": order.status,
        "shipping_status": order.shipping_status,
        "user_id": str(order.user_id),
    }


def sync_order_to_stripe(order: Order) -> None:
    if not stripe_sync_enabled():
        return
    metadata = order_stripe_metadata(order)
    description = f"Chili Platform order #{order.pk}"
    if order.stripe_payment_intent_id:
        try:
            update_payment_intent(
                order.stripe_payment_intent_id,
                {"description": description, "metadata": metadata},
            )
        except StripeError:
            pass
    if order.stripe_customer_id:
        try:
            update_customer(
                order.stripe_customer_id,
                {"metadata": {"last_order_id": str(order.pk)}},
            )
        except StripeError:
            pass


def set_shipping_status(order: Order, shipping_status: str) -> Order:
    order.shipping_status = shipping_status
    if shipping_status == Order.ShippingStatus.SHIPPED and order.status == Order.Status.PAID:
        order.status = Order.Status.FULFILLED
    elif (
        shipping_status == Order.ShippingStatus.PREPARING
        and order.status == Order.Status.FULFILLED
    ):
        order.status = Order.Status.PAID
    elif shipping_status == Order.ShippingStatus.NOT_SHIPPING and order.status == Order.Status.PENDING:
        pass
    order.save(update_fields=["shipping_status", "status", "updated_at"])
    sync_order_to_stripe(order)
    return order
