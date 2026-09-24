from __future__ import annotations

from django.contrib import admin, messages
from django.db import models as django_models
from django.forms import Textarea
from django.utils.html import format_html

from store.models import Order, OrderItem, Product, ProductImage
from store.stripe import StripeError
from store.sync import set_shipping_status, sync_product_to_stripe


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 2
    fields = ("image", "alt", "sort_order", "preview")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj: ProductImage) -> str:
        if not obj.image:
            return ""
        return format_html(
            '<img src="{}" alt="" style="height:64px;width:64px;object-fit:cover;background:#111" />',
            obj.image.url,
        )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "sku",
        "price_cents",
        "currency",
        "stock",
        "is_active",
        "stripe_product_id",
    )
    list_filter = ("is_active", "currency")
    search_fields = ("name", "sku", "short_description", "stripe_product_id")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "stripe_product_id", "stripe_price_id")
    actions = ("sync_to_stripe",)
    inlines = (ProductImageInline,)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "sku",
                    "price_cents",
                    "currency",
                    "stock",
                    "is_active",
                )
            },
        ),
        (
            "Copy",
            {
                "fields": ("short_description", "long_description"),
                "description": "Short copy appears on catalog cards. Long copy is Markdown on the product page.",
            },
        ),
        (
            "Stripe",
            {"fields": ("stripe_product_id", "stripe_price_id")},
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    formfield_overrides = {
        django_models.TextField: {"widget": Textarea(attrs={"rows": 16, "cols": 80})},
    }

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        try:
            sync_product_to_stripe(form.instance, request=request)
        except StripeError as exc:
            self.message_user(
                request,
                f"Saved locally, but Stripe catalog sync failed: {exc}",
                level=messages.ERROR,
            )

    @admin.action(description="Sync selected products to Stripe")
    def sync_to_stripe(self, request, queryset):
        synced = 0
        for product in queryset.prefetch_related("images"):
            try:
                sync_product_to_stripe(product)
                synced += 1
            except StripeError as exc:
                self.message_user(request, f"{product}: {exc}", level=messages.ERROR)
        self.message_user(request, f"Synced {synced} product(s) to Stripe.")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "unit_price_cents", "quantity")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "status",
        "shipping_status",
        "total_cents",
        "currency",
        "created_at",
    )
    list_filter = ("status", "shipping_status", "currency")
    search_fields = (
        "id",
        "user__username",
        "customer_email",
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
        "shipping_name",
    )
    inlines = [OrderItemInline]
    readonly_fields = (
        "user",
        "status",
        "currency",
        "total_cents",
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
        "created_at",
        "updated_at",
        "paid_at",
    )
    fieldsets = (
        (
            None,
            {"fields": ("user", "status", "shipping_status", "total_cents", "currency")},
        ),
        (
            "Stripe",
            {
                "fields": (
                    "stripe_checkout_session_id",
                    "stripe_payment_intent_id",
                    "stripe_customer_id",
                    "customer_email",
                )
            },
        ),
        (
            "Shipping address",
            {
                "fields": (
                    "shipping_name",
                    "shipping_line1",
                    "shipping_line2",
                    "shipping_city",
                    "shipping_state",
                    "shipping_postal_code",
                    "shipping_country",
                )
            },
        ),
        ("Timestamps", {"fields": ("paid_at", "created_at", "updated_at")}),
    )
    actions = ("mark_preparing", "mark_shipped")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        set_shipping_status(obj, obj.shipping_status)

    @admin.action(description="Mark selected orders as preparing to ship")
    def mark_preparing(self, request, queryset):
        count = 0
        for order in queryset.filter(status__in={Order.Status.PAID, Order.Status.FULFILLED}):
            set_shipping_status(order, Order.ShippingStatus.PREPARING)
            count += 1
        self.message_user(request, f"Marked {count} order(s) preparing to ship.")

    @admin.action(description="Mark selected paid orders as shipped")
    def mark_shipped(self, request, queryset):
        count = 0
        for order in queryset.filter(status__in={Order.Status.PAID, Order.Status.FULFILLED}):
            set_shipping_status(order, Order.ShippingStatus.SHIPPED)
            count += 1
        self.message_user(request, f"Marked {count} order(s) shipped.")
