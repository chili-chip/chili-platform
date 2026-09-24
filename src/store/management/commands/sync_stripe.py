from __future__ import annotations

from django.core.management.base import BaseCommand

from store.models import Order, Product
from store.stripe import StripeError
from store.sync import sync_order_to_stripe, sync_product_to_stripe


class Command(BaseCommand):
    help = "Push store products and paid-order metadata to Stripe."

    def handle(self, *args, **options):
        products = 0
        for product in Product.objects.all():
            try:
                sync_product_to_stripe(product)
                products += 1
                self.stdout.write(self.style.SUCCESS(f"Product {product.slug} → {product.stripe_product_id}"))
            except StripeError as exc:
                self.stderr.write(f"Product {product.slug} failed: {exc}")

        orders = 0
        for order in Order.objects.exclude(stripe_payment_intent_id=""):
            try:
                sync_order_to_stripe(order)
                orders += 1
            except StripeError as orig:
                self.stderr.write(f"Order {order.pk} failed: {orig}")

        self.stdout.write(self.style.SUCCESS(f"Stripe sync complete ({products} products, {orders} orders)."))
