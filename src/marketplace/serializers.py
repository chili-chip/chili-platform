from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from games.covers import absolute_media_url
from marketplace.models import Category, Listing, Purchase
from marketplace.services import min_paid_cents, normalize_tags


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "description")


class ListingSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    game = serializers.SerializerMethodField()
    owned = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = (
            "id",
            "slug",
            "description",
            "price_cents",
            "currency",
            "category",
            "tags",
            "game",
            "published",
            "owned",
            "created_at",
            "updated_at",
        )

    def get_category(self, listing: Listing) -> dict:
        return {"slug": listing.category.slug, "name": listing.category.name}

    def get_tags(self, listing: Listing) -> list[str]:
        return [tag.name for tag in listing.tags.all()]

    def get_game(self, listing: Listing) -> dict:
        game = listing.game
        cover = ""
        if game.cover:
            cover = absolute_media_url(game.cover.url, self.context.get("request"))
        return {
            "id": game.id,
            "title": game.title,
            "slug": game.slug,
            "cover": cover,
            "owner": listing.seller.username,
        }

    def get_owned(self, listing: Listing) -> bool:
        return listing.game_id in self.context.get("owned_ids", ())


class ListingWriteSerializer(serializers.Serializer):
    game = serializers.IntegerField(required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    price_cents = serializers.IntegerField(required=False, min_value=0)
    category = serializers.SlugField(required=False)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=40),
        required=False,
        allow_empty=True,
    )
    published = serializers.BooleanField(required=False)

    def validate_price_cents(self, value: int) -> int:
        if value != 0 and value < min_paid_cents():
            raise serializers.ValidationError("Paid games must cost at least $1.")
        return value

    def validate_tags(self, value: list[str]) -> list[str]:
        return normalize_tags(value)

    def validate(self, attrs):
        if self.context.get("creating"):
            missing = [field for field in ("game", "price_cents", "category") if field not in attrs]
            if missing:
                raise serializers.ValidationError({field: "This field is required." for field in missing})
        return attrs


class PurchaseSerializer(serializers.ModelSerializer):
    seller = serializers.CharField(source="seller.username", read_only=True)
    buyer = serializers.CharField(source="buyer.username", read_only=True)
    cover = serializers.SerializerMethodField()
    listing_slug = serializers.SerializerMethodField()
    game_id = serializers.IntegerField(read_only=True)
    available_at = serializers.SerializerMethodField()

    class Meta:
        model = Purchase
        fields = (
            "id",
            "status",
            "title",
            "price_cents",
            "currency",
            "platform_fee_cents",
            "processing_estimate_cents",
            "creator_credit_cents",
            "seller",
            "buyer",
            "cover",
            "listing_slug",
            "game_id",
            "paid_at",
            "available_at",
            "created_at",
        )

    def get_cover(self, purchase: Purchase) -> str:
        game = purchase.game
        if game is None or not game.cover:
            return ""
        return absolute_media_url(game.cover.url, self.context.get("request"))

    def get_listing_slug(self, purchase: Purchase) -> str:
        if purchase.listing_id and purchase.listing:
            return purchase.listing.slug
        if purchase.game_id and purchase.game:
            return purchase.game.slug
        return ""

    def get_available_at(self, purchase: Purchase):
        earning = getattr(purchase, "earning", None)
        if earning is None:
            return None
        return earning.available_at


class CheckoutConfirmSerializer(serializers.Serializer):
    session_id = serializers.CharField()


def _configured_fee(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class ConfigSerializer(serializers.Serializer):
    def to_representation(self, _instance):
        return {
            "currency": getattr(settings, "MARKETPLACE_CURRENCY", "usd"),
            "min_paid_cents": int(settings.MARKETPLACE_MIN_PAID_CENTS),
            "min_payout_cents": int(settings.MARKETPLACE_MIN_PAYOUT_CENTS),
            "hold_days": int(settings.MARKETPLACE_HOLD_DAYS),
            "platform_fee_bps": int(settings.MARKETPLACE_PLATFORM_FEE_BPS),
            "processing_fee_bps": _configured_fee(settings.MARKETPLACE_PROCESSING_FEE_BPS),
            "processing_fee_fixed_cents": _configured_fee(settings.MARKETPLACE_PROCESSING_FEE_FIXED_CENTS),
            "stripe_publishable_key": getattr(settings, "STRIPE_PUBLISHABLE_KEY", ""),
        }
