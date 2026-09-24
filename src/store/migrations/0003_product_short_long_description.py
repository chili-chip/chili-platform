from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("store", "0002_stripe_catalog_and_shipping"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="product",
                    name="short_description",
                    field=models.CharField(blank=True, default="", max_length=280),
                ),
                migrations.AddField(
                    model_name="product",
                    name="long_description",
                    field=models.TextField(
                        blank=True,
                        default="",
                        help_text="Markdown product copy shown on the product page.",
                    ),
                ),
                migrations.RemoveField(
                    model_name="product",
                    name="description",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE store_product ADD COLUMN short_description varchar(280) NOT NULL DEFAULT '';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE store_product ADD COLUMN long_description text NOT NULL DEFAULT '';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "UPDATE store_product SET short_description = substr(description, 1, 280) "
                        "WHERE ifnull(description, '') != '' AND ifnull(short_description, '') = '';"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
