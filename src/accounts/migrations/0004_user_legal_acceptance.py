from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_email_verified"),
    ]

    operations = [
        # Nullable on purpose. Accounts that already exist stay null and accept
        # the next time they register or confirm on login. Do not backfill.
        migrations.AddField(
            model_name="user",
            name="terms_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="privacy_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="seller_terms_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
