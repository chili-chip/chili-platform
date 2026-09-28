"""MCP read tools for the hardware store."""

from __future__ import annotations

from mcp_server import ModelQueryToolset

from app.mcp_helpers import AuthenticatedOwnerMixin
from store.models import Order, OrderItem, Product, ProductImage


class ActiveProductQueryTool(ModelQueryToolset):
    model = Product
    exclude_fields = ["stripe_product_id", "stripe_price_id"]
    search_fields = ["name", "slug", "short_description", "long_description", "sku"]
    extra_instructions = "Active catalog products only (public store read)."

    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class ProductImageQueryTool(ModelQueryToolset):
    model = ProductImage
    exclude_fields = ["image"]
    extra_instructions = "Metadata for product images on active products (file paths omitted)."

    def get_queryset(self):
        return super().get_queryset().filter(product__is_active=True)


class OrderQueryTool(AuthenticatedOwnerMixin, ModelQueryToolset):
    model = Order
    exclude_fields = [
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
        "stripe_customer_id",
        "customer_email",
        "shipping_name",
        "shipping_line1",
        "shipping_line2",
        "shipping_city",
        "shipping_state",
        "shipping_postal_code",
        "shipping_country",
    ]
    extra_instructions = (
        "Authenticated user's orders only; shipping and Stripe identifiers are excluded. "
        "Unauthenticated MCP clients see an empty collection."
    )


class OrderItemQueryTool(AuthenticatedOwnerMixin, ModelQueryToolset):
    model = OrderItem
    owner_field = "order__user"
    extra_instructions = "Line items for the authenticated user's orders only."
