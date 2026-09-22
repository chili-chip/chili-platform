from __future__ import annotations

from django.core.management.base import BaseCommand

from store.models import Product

DEFAULT_PRODUCTS = (
    {
        "name": "Chilichip Kit",
        "slug": "chilichip-kit",
        "sku": "CHIP-KIT",
        "description": "Handheld kit: board, buttons, and a starter shell.",
        "price_cents": 4999,
        "stock": 25,
        "is_active": True,
    },
    {
        "name": "vgc zero Shell",
        "slug": "vgc-zero-shell",
        "sku": "VGC-SHELL",
        "description": "Replacement shell for the vgc zero handheld.",
        "price_cents": 2999,
        "stock": 40,
        "is_active": True,
    },
)


class Command(BaseCommand):
    help = "Seed default Chilichip store products."

    def handle(self, *args, **options):
        created = 0
        for payload in DEFAULT_PRODUCTS:
            _, was_created = Product.objects.get_or_create(
                slug=payload["slug"],
                defaults=payload,
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created product: {payload['name']}"))
            else:
                self.stdout.write(f"Exists: {payload['name']}")
        self.stdout.write(self.style.SUCCESS(f"Store seed complete ({created} new)."))
