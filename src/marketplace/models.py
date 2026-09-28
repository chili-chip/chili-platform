from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from games.models import Game


class Category(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Listing(models.Model):
    game = models.OneToOneField(Game, on_delete=models.CASCADE, related_name="listing")
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True, default="")
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="usd")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="listings")
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.slug


class ListingTag(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="tags")
    name = models.SlugField(max_length=24)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["listing", "name"], name="uniq_listing_tag"),
        ]

    def __str__(self) -> str:
        return self.name


class Purchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"
        DISPUTED = "disputed", "Disputed"
        CANCELED = "canceled", "Canceled"
        FAILED = "failed", "Failed"

    listing = models.ForeignKey(
        Listing,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchases",
    )
    game = models.ForeignKey(
        Game,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="purchases",
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="game_purchases",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="game_sales",
    )
    title = models.CharField(max_length=160)
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="usd")
    platform_fee_cents = models.PositiveIntegerField(default=0)
    processing_estimate_cents = models.PositiveIntegerField(default=0)
    creator_credit_cents = models.PositiveIntegerField(default=0)
    refunded_cents = models.PositiveIntegerField(default=0)
    disputed_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default="")
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    stripe_charge_id = models.CharField(max_length=255, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["buyer", "game"],
                condition=models.Q(status__in=["pending", "paid", "refunded", "disputed"]),
                name="uniq_marketplace_purchase_per_game",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class ConnectedAccount(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connected_account",
    )
    stripe_account_id = models.CharField(max_length=255, unique=True)
    transfers_status = models.CharField(max_length=32, blank=True, default="")
    payouts_status = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.stripe_account_id


class Payout(models.Model):
    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    amount_cents = models.PositiveIntegerField()
    reversed_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PAID)
    stripe_transfer_id = models.CharField(max_length=255, unique=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.stripe_transfer_id


class Earning(models.Model):
    class Status(models.TextChoices):
        HELD = "held", "Held"
        AVAILABLE = "available", "Available"
        PAID = "paid", "Paid"
        REVERSED = "reversed", "Reversed"

    purchase = models.OneToOneField(Purchase, on_delete=models.CASCADE, related_name="earning")
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="earnings",
    )
    payout = models.ForeignKey(
        Payout,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="earnings",
    )
    credit_cents = models.PositiveIntegerField()
    unpaid_cents = models.PositiveIntegerField()
    settled_cents = models.PositiveIntegerField(default=0)
    reversed_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.HELD)
    available_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["available_at", "id"]

    def __str__(self) -> str:
        return f"{self.creator_id}:{self.credit_cents}"


RATING_COMMENT_MAX_LENGTH = 500


class Rating(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="game_ratings",
    )
    stars = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=RATING_COMMENT_MAX_LENGTH, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="uniq_game_rating"),
            models.CheckConstraint(
                condition=models.Q(stars__gte=1, stars__lte=5),
                name="rating_stars_1_to_5",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.game_id}:{self.stars}"


@receiver(post_save, sender=Game)
def sync_listing_slug(sender, instance: Game, **kwargs) -> None:
    Listing.objects.filter(game_id=instance.pk).exclude(slug=instance.slug).update(slug=instance.slug)
