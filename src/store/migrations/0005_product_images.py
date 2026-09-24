from django.db import migrations, models
import django.db.models.deletion

import store.models


class Migration(migrations.Migration):
    dependencies = [
        ("store", "0004_drop_product_description"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductImage",
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
                (
                    "image",
                    models.FileField(
                        help_text="JPEG, PNG, WebP, or GIF. 8 MB max.",
                        upload_to=store.models.product_image_upload_to,
                        validators=[store.models.validate_product_image],
                    ),
                ),
                ("alt", models.CharField(blank=True, default="", max_length=120)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="images",
                        to="store.product",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="product",
                    name="image_url",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE store_product DROP COLUMN image_url;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
