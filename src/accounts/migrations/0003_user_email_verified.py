from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_user_stripe_customer_id"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="email_verified",
                    field=models.BooleanField(
                        default=False,
                        help_text=(
                            "Public registration starts false. Existing rows and "
                            "the bootstrap admin are true."
                        ),
                    ),
                ),
            ],
            database_operations=[
                # DEFAULT 1 marks accounts that already exist. New rows written
                # by the ORM use the model default, which is false.
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE accounts_user "
                        "ADD COLUMN email_verified bool NOT NULL DEFAULT 1;"
                    ),
                    reverse_sql="ALTER TABLE accounts_user DROP COLUMN email_verified;",
                ),
            ],
        ),
    ]
