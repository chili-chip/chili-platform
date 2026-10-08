from __future__ import annotations

from django.core.management.base import BaseCommand

from store.models import Category, DeliveryOption, Product
from store.stripe import StripeError
from store.sync import sync_product_to_stripe

DEFAULT_CATEGORIES = (
    {"slug": "kits", "name": "Kits", "sort_order": 1, "description": "Everything to build a handheld."},
    {"slug": "pcbs", "name": "PCBs", "sort_order": 2, "description": "Bare and assembled boards."},
    {"slug": "modules", "name": "MCUs & modules", "sort_order": 3, "description": "Microcontroller boards and add-on modules."},
    {"slug": "shells", "name": "Shells & parts", "sort_order": 4, "description": "Cases, buttons, and spare parts."},
    {"slug": "accessories", "name": "Accessories", "sort_order": 5, "description": "Cables, tools, and extras."},
)

DEFAULT_DELIVERY_OPTIONS = (
    {
        "slug": "eu-standard",
        "name": "EU standard parcel",
        "description": "Tracked parcel to any EU country.",
        "estimate": "3-7 business days",
        "price_cents": 599,
        "free_over_cents": 7500,
        "sort_order": 1,
    },
    {
        "slug": "pl-courier",
        "name": "Courier (Poland)",
        "description": "Next-day courier within Poland.",
        "estimate": "1-2 business days",
        "price_cents": 1499,
        "countries": "PL",
        "sort_order": 2,
    },
    {
        "slug": "local-pickup",
        "name": "Local pickup",
        "description": "Collect your order in person. We email you when it is ready.",
        "price_cents": 0,
        "requires_address": False,
        "sort_order": 3,
    },
)

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
        "category": "kits",
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
        "category": "shells",
        "price_cents": 2999,
        "stock": 40,
        "is_active": True,
    },
)

COPY_FIELDS = ("name", "sku", "short_description", "long_description")


class Command(BaseCommand):
    help = "Seed default Chilichip store products."

    def handle(self, *args, **options):
        categories = {}
        for payload in DEFAULT_CATEGORIES:
            category, _ = Category.objects.get_or_create(
                slug=payload["slug"],
                defaults=payload,
            )
            categories[category.slug] = category

        for payload in DEFAULT_DELIVERY_OPTIONS:
            DeliveryOption.objects.get_or_create(slug=payload["slug"], defaults=payload)

        created = 0
        for raw in DEFAULT_PRODUCTS:
            payload = dict(raw)
            payload["category"] = categories.get(payload.pop("category", ""))
            product, was_created = Product.objects.get_or_create(
                slug=payload["slug"],
                defaults=payload,
            )
            if not was_created:
                changed = False
                if product.category_id is None and payload["category"] is not None:
                    product.category = payload["category"]
                    changed = True
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
