"""Email-only auth migration, step 4/4.

Drop the ``username`` column entirely. The model now sets ``username = None``
and ``USERNAME_FIELD = "email"``; this removes the now-orphaned column from the
schema. Also re-declares the field-level options that change with email-only
auth (``USERNAME_FIELD`` / ``EMAIL_FIELD`` are model-meta, captured implicitly
by the manager + field swaps in 0010-0012).

The orphaned allauth tables (account_emailaddress / account_emailconfirmation /
socialaccount_*) are intentionally LEFT in place — harmless, droppable later.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("customuser", "0012_alter_customuser_email_unique"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="customuser",
            name="username",
        ),
    ]
