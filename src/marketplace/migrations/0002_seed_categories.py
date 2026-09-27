from django.db import migrations

CATEGORIES = (
    ("action", "Action", "Fast games with timing and reflexes."),
    ("adventure", "Adventure", "Exploration and places to walk through."),
    ("arcade", "Arcade", "Short scores, loops, and one-more-try games."),
    ("experimental", "Experimental", "Odd mechanics and sketches."),
    ("narrative", "Narrative", "Dialogue, stories, and characters."),
    ("puzzle", "Puzzle", "Problems, locks, and quiet solutions."),
)


def seed_categories(apps, schema_editor):
    Category = apps.get_model("marketplace", "Category")
    for slug, name, description in CATEGORIES:
        Category.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "description": description},
        )


def unseed_categories(apps, schema_editor):
    Category = apps.get_model("marketplace", "Category")
    Category.objects.filter(slug__in=[slug for slug, _name, _description in CATEGORIES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
