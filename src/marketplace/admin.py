from django.contrib import admin

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


class ListingTagInline(admin.TabularInline):
    model = ListingTag
    extra = 0


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("slug", "seller", "price_cents", "currency", "published", "category")
    list_filter = ("published", "category", "currency")
    search_fields = ("slug", "game__title", "seller__username")
    inlines = [ListingTagInline]


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ("id", "game", "user", "stars", "created_at")
    list_filter = ("stars",)
    search_fields = ("user__username", "game__title", "comment")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "buyer", "seller", "price_cents", "status", "paid_at")
    list_filter = ("status", "currency")
    search_fields = ("title", "buyer__username", "seller__username", "stripe_payment_intent_id")


@admin.register(Earning)
class EarningAdmin(admin.ModelAdmin):
    list_display = ("id", "creator", "credit_cents", "unpaid_cents", "settled_cents", "status", "available_at")
    list_filter = ("status",)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ("id", "creator", "amount_cents", "reversed_cents", "status", "stripe_transfer_id")


@admin.register(ConnectedAccount)
class ConnectedAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "stripe_account_id", "transfers_status", "payouts_status")
