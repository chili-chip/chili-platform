from django.db import migrations


def release_listed_games(apps, schema_editor):
    Game = apps.get_model("games", "Game")
    Listing = apps.get_model("marketplace", "Listing")
    game_ids = list(Listing.objects.values_list("game_id", flat=True))
    if game_ids:
        Game.objects.filter(pk__in=game_ids, released=False).update(released=True)


class Migration(migrations.Migration):
    dependencies = [
        ("games", "0002_game_released"),
        ("marketplace", "0002_seed_categories"),
    ]

    operations = [
        migrations.RunPython(release_listed_games, migrations.RunPython.noop),
    ]
