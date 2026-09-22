from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Product(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True, default="")
    sku = models.CharField(max_length=40, blank=True, default="")
    price_cents = models.PositiveIntegerField(
        help_text="Unit price in the smallest currency unit (e.g. 4999 = $49.99).",
    )
    currency = models.CharField(max_length=3, default="usd")
    image_url = models.URLField(blank=True, default="")
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "product"
            slug = base
            index = 2
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{index}"
                index += 1
            self.slug = slug
        if not self.currency:
            self.currency = getattr(settings, "STORE_CURRENCY", "usd")
        self.currency = self.currency.lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        CANCELED = "canceled", "Canceled"
        FAILED = "failed", "Failed"
        FULFILLED = "fulfilled", "Fulfilled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_orders",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    currency = models.CharField(max_length=3, default="usd")
    total_cents = models.PositiveIntegerField(default=0)
    stripe_checkout_session_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    customer_email = models.EmailField(blank=True, default="")
    shipping_name = models.CharField(max_length=120, blank=True, default="")
    shipping_line1 = models.CharField(max_length=200, blank=True, default="")
    shipping_line2 = models.CharField(max_length=200, blank=True, default="")
    shipping_city = models.CharField(max_length=120, blank=True, default="")
    shipping_state = models.CharField(max_length=120, blank=True, default="")
    shipping_postal_code = models.CharField(max_length=20, blank=True, default="")
    shipping_country = models.CharField(max_length=2, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Order {self.pk} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    product_name = models.CharField(max_length=120)
    unit_price_cents = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product_name}"

    @property
    def line_total_cents(self) -> int:
        return self.unit_price_cents * self.quantity
