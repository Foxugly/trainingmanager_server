"""Email-only auth migration, step 3/4.

Make ``email`` non-blank and UNIQUE — it is about to become the
``USERNAME_FIELD``. Safe on the live ``tm`` DB: the two existing users both
have clean, distinct, non-null emails (audited), so the unique index builds
without conflict and no backfill/dedup is required.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customuser", "0011_backfill_email_confirmed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customuser",
            name="email",
            field=models.EmailField(
                max_length=254, unique=True, verbose_name="email address"
            ),
        ),
    ]
