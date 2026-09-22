from __future__ import annotations

from rest_framework import serializers

from store.models import Order, OrderItem, Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "sku",
            "price_cents",
            "currency",
            "image_url",
            "stock",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "slug", "created_at", "updated_at")

    def validate_price_cents(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError("Price must be at least 1 cent.")
        return value


class CheckoutItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=99)


class CheckoutCreateSerializer(serializers.Serializer):
    items = CheckoutItemSerializer(many=True, allow_empty=False)

    def validate_items(self, items):
        seen: set[int] = set()
        merged: dict[int, dict] = {}
        for item in items:
            product = item["product"]
            if product.id in seen:
                merged[product.id]["quantity"] += item["quantity"]
            else:
                seen.add(product.id)
                merged[product.id] = {"product": product, "quantity": item["quantity"]}
        return list(merged.values())


class CheckoutConfirmSerializer(serializers.Serializer):
    session_id = serializers.CharField(max_length=255)


class OrderItemSerializer(serializers.ModelSerializer):
    line_total_cents = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product",
            "product_name",
            "unit_price_cents",
            "quantity",
            "line_total_cents",
        )
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "status",
            "currency",
            "total_cents",
            "stripe_checkout_session_id",
            "customer_email",
            "shipping_name",
            "shipping_line1",
            "shipping_line2",
            "shipping_city",
            "shipping_state",
            "shipping_postal_code",
            "shipping_country",
            "items",
            "created_at",
            "updated_at",
            "paid_at",
        )
        read_only_fields = fields

