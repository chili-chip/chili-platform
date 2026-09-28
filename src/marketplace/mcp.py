"""MCP read tools for the game marketplace."""

from __future__ import annotations

from django.db.models import Q
from mcp_server import ModelQueryToolset

from app.mcp_helpers import AuthenticatedOwnerMixin, mcp_user
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


class MarketplaceCategoryQueryTool(ModelQueryToolset):
    model = Category
    search_fields = ["name", "slug", "description"]


class PublishedListingQueryTool(ModelQueryToolset):
    model = Listing
    search_fields = ["slug", "description"]
    extra_instructions = "Published marketplace listings only."

    def get_queryset(self):
        return super().get_queryset().filter(published=True)


class ListingTagQueryTool(ModelQueryToolset):
    model = ListingTag
    search_fields = ["name"]
    extra_instructions = "Tags on published listings."

    def get_queryset(self):
        return super().get_queryset().filter(listing__published=True)


class GameRatingQueryTool(ModelQueryToolset):
    model = Rating
    search_fields = ["comment"]
    extra_instructions = "Star ratings and comments on released games."


class BuyerSellerPurchaseQueryTool(ModelQueryToolset):
    model = Purchase
    exclude_fields = [
        "stripe_checkout_session_id",
        "stripe_payment_intent_id",
        "stripe_charge_id",
    ]
    extra_instructions = (
        "Purchases where the authenticated user is buyer or seller; Stripe IDs excluded."
    )

    def get_queryset(self):
        user = mcp_user(self.request)
        if user is None:
            return self.model._default_manager.none()
        return super().get_queryset().filter(Q(buyer=user) | Q(seller=user))


class ConnectedAccountQueryTool(AuthenticatedOwnerMixin, ModelQueryToolset):
    model = ConnectedAccount
    exclude_fields = ["stripe_account_id"]
    extra_instructions = "Payout onboarding status for the authenticated creator (Stripe account id hidden)."


class CreatorPayoutQueryTool(AuthenticatedOwnerMixin, ModelQueryToolset):
    model = Payout
    owner_field = "creator"
    exclude_fields = ["stripe_transfer_id", "idempotency_key"]
    extra_instructions = "Payout history for the authenticated creator."


class CreatorEarningQueryTool(AuthenticatedOwnerMixin, ModelQueryToolset):
    model = Earning
    owner_field = "creator"
    extra_instructions = "Earnings ledger for the authenticated creator."
