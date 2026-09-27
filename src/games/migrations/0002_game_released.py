from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("games", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="released",
            field=models.BooleanField(
                default=False,
                help_text="A project is unreleased and cannot be sold. Release keeps this Bitsy data and does not list the game.",
            ),
        ),
    ]
