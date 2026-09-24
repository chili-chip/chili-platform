from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("store", "0003_product_short_long_description"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE store_product DROP COLUMN description;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
