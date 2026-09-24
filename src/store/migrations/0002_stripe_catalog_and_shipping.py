from django.db import migrations, models


def backfill_shipping_status(apps, schema_editor):
    Order = apps.get_model("store", "Order")
    Order.objects.filter(status="fulfilled").update(shipping_status="shipped")
    Order.objects.filter(status="paid").update(shipping_status="preparing")
    Order.objects.filter(status__in=["canceled", "failed"]).update(shipping_status="not_shipping")


class Migration(migrations.Migration):
    dependencies = [
        ("store", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="product",
                    name="stripe_product_id",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
                migrations.AddField(
                    model_name="product",
                    name="stripe_price_id",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
                migrations.AddField(
                    model_name="order",
                    name="shipping_status",
                    field=models.CharField(
                        choices=[
                            ("awaiting_payment", "Awaiting payment"),
                            ("preparing", "Preparing to ship"),
                            ("shipped", "Shipped"),
                            ("not_shipping", "Not shipping"),
                        ],
                        db_index=True,
                        default="awaiting_payment",
                        max_length=24,
                    ),
                ),
                migrations.AddField(
                    model_name="order",
                    name="stripe_customer_id",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE store_product ADD COLUMN stripe_product_id varchar(255) NOT NULL DEFAULT '';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE store_product ADD COLUMN stripe_price_id varchar(255) NOT NULL DEFAULT '';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE store_order ADD COLUMN shipping_status varchar(24) NOT NULL DEFAULT 'awaiting_payment';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE store_order ADD COLUMN stripe_customer_id varchar(255) NOT NULL DEFAULT '';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
        migrations.RunPython(backfill_shipping_status, migrations.RunPython.noop),
    ]
