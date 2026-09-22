from __future__ import annotations

from django.contrib import admin

from store.models import Order, OrderItem, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "price_cents", "currency", "stock", "is_active")
    list_filter = ("is_active", "currency")
    search_fields = ("name", "sku", "description")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "unit_price_cents", "quantity")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "total_cents", "currency", "created_at")
    list_filter = ("status", "currency")
    search_fields = (
        "id",
        "user__username",
        "customer_email",
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
    )
    inlines = [OrderItemInline]
    readonly_fields = (
        "user",
        "currency",
        "total_cents",
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
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
    actions = ("mark_fulfilled",)

    @admin.action(description="Mark selected paid orders as fulfilled")
    def mark_fulfilled(self, request, queryset):
        updated = queryset.filter(status=Order.Status.PAID).update(status=Order.Status.FULFILLED)
        self.message_user(request, f"Marked {updated} order(s) fulfilled.")
