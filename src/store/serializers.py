from __future__ import annotations

from rest_framework import serializers

from store.models import (
    Order,
    OrderItem,
    Product,
    ProductImage,
    absolute_media_url,
    product_image_urls,
)
from store.stripe import StripeError
from store.sync import sync_product_to_stripe


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ("id", "url", "alt", "sort_order")
        read_only_fields = fields

    def get_url(self, obj: ProductImage) -> str:
        if not obj.image:
            return ""
        request = self.context.get("request")
        return absolute_media_url(obj.image.url, request)


class ProductSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "short_description",
            "long_description",
            "sku",
            "price_cents",
            "currency",
            "images",
            "image_url",
            "stock",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "slug", "images", "image_url", "created_at", "updated_at")

    def get_image_url(self, obj: Product) -> str:
        urls = product_image_urls(obj, request=self.context.get("request"), limit=1)
        return urls[0] if urls else ""

    def validate_price_cents(self, value: int) -> int:
        if value < 1:
            raise serializers.ValidationError("Price must be at least 1 cent.")
        return value

    def create(self, validated_data):
        product = super().create(validated_data)
        try:
            sync_product_to_stripe(product)
        except StripeError:
            pass
        product.refresh_from_db()
        return product

    def update(self, instance, validated_data):
        product = super().update(instance, validated_data)
        try:
            sync_product_to_stripe(product)
        except StripeError:
            pass
        product.refresh_from_db()
        return product


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
            "shipping_status",
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

