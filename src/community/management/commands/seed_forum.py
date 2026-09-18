from __future__ import annotations

from django.core.management.base import BaseCommand

from community.models import ForumCategory

DEFAULT_CATEGORIES = (
    ("General", "general", "Hangouts, intros, and off-topic chili talk."),
    (
        "vgc zero Hardware",
        "vgc-zero-hardware",
        "Handheld builds, shells, buttons, and Chilichip kits.",
    ),
    ("Game Dev", "game-dev", "Tiles, dialogue, tools, and shipping to the vgc zero."),
    (
        "Marketplace Discussions",
        "marketplace-discussions",
        "Releases, pricing, licenses, and creator support.",
    ),
)


class Command(BaseCommand):
    help = "Seed default Chili Platform forum categories."

    def handle(self, *args, **options):
        created = 0
        for name, slug, description in DEFAULT_CATEGORIES:
            _, was_created = ForumCategory.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "description": description},
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created category: {name}"))
            else:
                self.stdout.write(f"Exists: {name}")
        self.stdout.write(self.style.SUCCESS(f"Seed complete ({created} new)."))
