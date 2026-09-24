from __future__ import annotations

from django.core.management.base import BaseCommand

from store.models import Product
from store.stripe import StripeError
from store.sync import sync_product_to_stripe

DEFAULT_PRODUCTS = (
    {
        "name": "Chilichip Kit",
        "slug": "chilichip-kit",
        "sku": "CHIP-KIT",
        "short_description": "Handheld kit: board, buttons, and a starter shell.",
        "long_description": """The **Chilichip Kit** is the full starter pack for the vgc zero.

## In the box

- Main board
- Face buttons and membrane
- Starter shell

## Notes

Flash firmware after assembly. Sold as a hobby kit — not a finished consumer handheld.

Need a spare lid later? The [vgc zero Shell](/store/vgc-zero-shell) is sold separately.
""",
        "price_cents": 4999,
        "stock": 25,
        "is_active": True,
    },
    {
        "name": "vgc zero Shell",
        "slug": "vgc-zero-shell",
        "sku": "VGC-SHELL",
        "short_description": "Replacement shell for the vgc zero handheld.",
        "long_description": """A **drop-in replacement shell** for the vgc zero.

## Fits

- Chilichip Kit boards
- Stock button layout

## Finish

Matte plastic with room for the stock screen and d-pad. Swap it when the original lid cracks, or pick a spare colorway for a second build.
""",
        "price_cents": 2999,
        "stock": 40,
        "is_active": True,
    },
)

COPY_FIELDS = ("name", "sku", "short_description", "long_description")


class Command(BaseCommand):
    help = "Seed default Chilichip store products."

    def handle(self, *args, **options):
        created = 0
        for payload in DEFAULT_PRODUCTS:
            product, was_created = Product.objects.get_or_create(
                slug=payload["slug"],
                defaults=payload,
            )
            if not was_created:
                changed = False
                for key in COPY_FIELDS:
                    value = payload[key]
                    if value and not getattr(product, key):
                        setattr(product, key, value)
                        changed = True
                if changed:
                    product.save()
            try:
                sync_product_to_stripe(product)
            except StripeError as exc:
                self.stderr.write(f"Stripe sync failed for {payload['name']}: {exc}")
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created product: {payload['name']}"))
            else:
                self.stdout.write(f"Exists: {payload['name']}")
        self.stdout.write(self.style.SUCCESS(f"Store seed complete ({created} new)."))
