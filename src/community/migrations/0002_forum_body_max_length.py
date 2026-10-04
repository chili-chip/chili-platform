from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="forumcomment",
            name="content",
            field=models.TextField(max_length=4000),
        ),
        migrations.AlterField(
            model_name="forumpost",
            name="content",
            field=models.TextField(max_length=4000),
        ),
    ]
