from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="stripe_customer_id",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE accounts_user ADD COLUMN stripe_customer_id varchar(255) NOT NULL DEFAULT '';",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
