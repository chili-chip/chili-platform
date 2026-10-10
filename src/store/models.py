from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.text import slugify

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def product_image_upload_to(instance: ProductImage, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in ALLOWED_IMAGE_EXTS:
        ext = "jpg"
    product_id = instance.product_id or "new"
    return f"store/products/{product_id}/{uuid4().hex}.{ext}"


def validate_product_image(value) -> None:
    name = getattr(value, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext and ext not in ALLOWED_IMAGE_EXTS:
        raise ValidationError("Upload a JPEG, PNG, WebP, or GIF.")
    content_type = getattr(value, "content_type", "") or ""
    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError("Upload a JPEG, PNG, WebP, or GIF.")
    size = getattr(value, "size", 0) or 0
    if size > MAX_IMAGE_BYTES:
        raise ValidationError("Each picture must be 8 MB or smaller.")


def absolute_media_url(url: str, request=None) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if request is not None:
        return request.build_absolute_uri(url)
    base = getattr(settings, "PUBLIC_BASE_URL", "http://localhost:8787").rstrip("/")
    path = url if url.startswith("/") else f"/{url}"
    return f"{base}{path}"


def product_image_urls(product: Product, *, request=None, limit: int = 8) -> list[str]:
    urls: list[str] = []
    for image in product.images.all():
        if not image.image:
            continue
        urls.append(absolute_media_url(image.image.url, request))
        if len(urls) >= limit:
            break
    return urls


class Category(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.CharField(max_length=280, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "category"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    short_description = models.CharField(max_length=280, blank=True, default="")
    long_description = models.TextField(
        blank=True,
        default="",
        help_text="Markdown product copy shown on the product page.",
    )
    sku = models.CharField(max_length=40, blank=True, default="")
    price_cents = models.PositiveIntegerField(
        help_text="Unit price in the smallest currency unit (e.g. 4999 = €49.99).",
    )
    currency = models.CharField(max_length=3, default="eur")
    stock = models.PositiveIntegerField(default=0)
    is_digital = models.BooleanField(
        default=False,
        help_text="Delivered digitally. Orders with only digital items skip delivery.",
    )
    delivery_options = models.ManyToManyField(
        "DeliveryOption",
        blank=True,
        related_name="products",
        help_text="Delivery methods this product can ship with. Blank allows every active option.",
    )
    is_active = models.BooleanField(default=True)
    stripe_product_id = models.CharField(max_length=255, blank=True, default="")
    stripe_price_id = models.CharField(max_length=255, blank=True, default="")
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
            self.currency = getattr(settings, "STORE_CURRENCY", "eur")
        self.currency = self.currency.lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.FileField(
        upload_to=product_image_upload_to,
        validators=[validate_product_image],
        help_text="JPEG, PNG, WebP, or GIF. 8 MB max.",
    )
    alt = models.CharField(max_length=120, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "picture"
        verbose_name_plural = "pictures"

    def __str__(self) -> str:
        return self.alt or f"Image {self.pk} for {self.product_id}"


class DeliveryOption(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.CharField(max_length=280, blank=True, default="")
    estimate = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text='Shown to shoppers, e.g. "2-4 business days".',
    )
    price_cents = models.PositiveIntegerField(
        default=0,
        help_text="Delivery fee in cents. 0 means free.",
    )
    free_over_cents = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Delivery is free when the items total at least this many cents.",
    )
    countries = models.CharField(
        max_length=400,
        blank=True,
        default="",
        help_text="Comma-separated ISO country codes. Blank means every store shipping country.",
    )
    requires_address = models.BooleanField(
        default=True,
        help_text="Untick for local pickup so checkout does not ask for an address.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "price_cents", "name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "delivery"
        self.countries = ",".join(self.country_list())
        super().save(*args, **kwargs)

    def country_list(self) -> list[str]:
        return [code.strip().upper() for code in self.countries.split(",") if code.strip()]

    def fee_for(self, subtotal_cents: int) -> int:
        if self.free_over_cents is not None and subtotal_cents >= self.free_over_cents:
            return 0
        return self.price_cents

    def __str__(self) -> str:
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        CANCELED = "canceled", "Canceled"
        FAILED = "failed", "Failed"
        FULFILLED = "fulfilled", "Fulfilled"

    class ShippingStatus(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
        PREPARING = "preparing", "Preparing to ship"
        SHIPPED = "shipped", "Shipped"
        NOT_SHIPPING = "not_shipping", "Not shipping"

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
    shipping_status = models.CharField(
        max_length=24,
        choices=ShippingStatus.choices,
        default=ShippingStatus.AWAITING_PAYMENT,
        db_index=True,
    )
    currency = models.CharField(max_length=3, default="eur")
    total_cents = models.PositiveIntegerField(default=0)
    delivery_option = models.ForeignKey(
        DeliveryOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    delivery_name = models.CharField(max_length=80, blank=True, default="")
    delivery_cents = models.PositiveIntegerField(default=0)
    stripe_checkout_session_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
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

    @property
    def items_cents(self) -> int:
        return self.total_cents - self.delivery_cents


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


RATING_COMMENT_MAX_LENGTH = 500


class ProductRating(models.Model):
    """A shopper's 1-5 star rating of a product, with an optional comment.

    Works like game ratings in the marketplace: one per person per product,
    and it can't be edited once posted.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_ratings",
    )
    stars = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=RATING_COMMENT_MAX_LENGTH, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "product"], name="uniq_product_rating"),
            models.CheckConstraint(
                condition=models.Q(stars__gte=1, stars__lte=5),
                name="product_rating_stars_1_to_5",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.product_id}:{self.stars}"


@receiver(post_delete, sender=ProductImage)
def delete_product_image_file(sender, instance: ProductImage, **kwargs) -> None:
    if instance.image:
        instance.image.delete(save=False)
