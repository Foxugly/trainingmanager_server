"""Email-only auth migration, step 1/4.

Add the ``email_confirmed`` boolean (replaces allauth's EmailAddress.verified
gate) and swap the model manager to the email-keyed ``CustomUserManager``.

Split across four migrations (0010-0013) so the data backfill (0011) runs
AFTER the column exists but BEFORE ``email`` is made unique / ``username`` is
dropped — keeping every step Postgres-safe on the live ``tm`` database.
"""

from django.db import migrations, models

import customuser.models


class Migration(migrations.Migration):

    dependencies = [
        ("customuser", "0009_customuser_magic_link_nonce"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="email_confirmed",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True once the user has proven control of their email address "
                    "(registration confirmation, password reset, or a verified email "
                    "change). The login + magic-link gates require this. Replaces the "
                    "former allauth EmailAddress.verified flag."
                ),
            ),
        ),
        migrations.AlterModelManagers(
            name="customuser",
            managers=[
                ("objects", customuser.models.CustomUserManager()),
            ],
        ),
    ]
