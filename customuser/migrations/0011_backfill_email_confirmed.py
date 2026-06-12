"""Email-only auth migration, step 2/4 — data backfill.

Set ``email_confirmed=True`` for every user who currently has a verified
PRIMARY allauth ``EmailAddress`` row. Everyone else stays False (the model
default) and must re-confirm via the normal flow.

Read via RAW SQL, NOT ``apps.get_model('account', 'EmailAddress')``: allauth is
being REMOVED from INSTALLED_APPS in this same change, so the historical model
no longer resolves through the migration state. The allauth TABLES are left in
place (orphaned, harmless) — only the app registration is gone.

DEFENSIVE: fresh / CI / SQLite test databases never had allauth installed, so
``account_emailaddress`` may not exist. We probe for the table first (catching
the DB error portably) and no-op when it is absent — there is nothing to
backfill there.
"""

from django.db import migrations


def _table_exists(cursor, connection, table_name: str) -> bool:
    """Portable 'does this table exist?' check.

    Uses the connection's introspection (works on both PostgreSQL and SQLite)
    so we don't hardcode information_schema (absent on SQLite).
    """
    try:
        return table_name in connection.introspection.table_names(cursor)
    except Exception:  # pragma: no cover — defensive
        return False


def backfill_email_confirmed(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        if not _table_exists(cursor, connection, "account_emailaddress"):
            # No allauth tables (fresh / test DB) — nothing to backfill.
            return

        # Verified PRIMARY addresses identify the users who proved email
        # ownership under the old allauth gate. ``"primary"`` is quoted because
        # it is a reserved word in PostgreSQL.
        try:
            cursor.execute(
                'SELECT user_id FROM account_emailaddress '
                'WHERE verified AND "primary"'
            )
            user_ids = [row[0] for row in cursor.fetchall()]
        except Exception:  # pragma: no cover — defensive (table shape drift)
            return

    if not user_ids:
        return

    CustomUser = apps.get_model("customuser", "CustomUser")
    CustomUser.objects.filter(pk__in=user_ids).update(email_confirmed=True)


def noop_reverse(apps, schema_editor):
    """Irreversible data backfill — provide a no-op so the migration can be
    unapplied without error (we do not try to reconstruct allauth state)."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ("customuser", "0010_customuser_email_confirmed_alter_managers"),
    ]

    operations = [
        migrations.RunPython(backfill_email_confirmed, noop_reverse),
    ]
