import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_legal_acceptance"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="display_name",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.CreateModel(
            name="UserSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("locale", models.CharField(blank=True, default="en", max_length=10)),
                (
                    "theme",
                    models.CharField(
                        choices=[
                            ("system", "System"),
                            ("light", "Light"),
                            ("dark", "Dark"),
                        ],
                        default="system",
                        max_length=10,
                    ),
                ),
                ("newsletter_opt_in", models.BooleanField(default=False)),
                ("newsletter_updated_at", models.DateTimeField(blank=True, null=True)),
                ("show_bio", models.BooleanField(default=True)),
                ("show_joined", models.BooleanField(default=True)),
                ("show_games", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "user settings",
            },
        ),
    ]
