"""Delete audit-log rows older than a retention window.

Audit rows (``audit.AuditLogEntry``) accumulate forever; some carry PII-adjacent
labels in ``target_repr`` (e.g. a member-name snapshot on a delete/anonymize
action), so an unbounded log is both a storage and a privacy concern. This
command caps their lifetime: it deletes every row whose ``created_at`` is older
than ``--days``, in batches so a large purge never opens one giant transaction.

Idempotent: re-running after a full purge deletes 0 rows and exits 0.
``--dry-run`` counts what WOULD be deleted without deleting. Operator-facing
logs only (no user-facing i18n).

Exclusion: rows whose action is in ``audit.models.NON_PURGEABLE_ACTIONS``
(currently the offered-access grant/revoke pair) are kept regardless of age —
they trace the grant of a paid entitlement, whose commercial value survives
the ordinary retention window. This command never deletes them.

Intended to run from a weekly systemd timer (deploy/systemd/tm-audit-purge.*).
"""

import datetime
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLogEntry, NON_PURGEABLE_ACTIONS

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = getattr(settings, "AUDIT_RETENTION_DAYS", 365)


class Command(BaseCommand):
    help = "Delete audit-log entries older than the retention window (--days)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Delete rows older than this many days (default {DEFAULT_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Rows deleted per transaction (default 1000).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be deleted; delete nothing.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days < 0:
            raise CommandError("--days must be >= 0.")
        batch_size = max(1, options["batch_size"])
        cutoff = timezone.now() - datetime.timedelta(days=days)

        old_qs = AuditLogEntry.objects.filter(created_at__lt=cutoff).exclude(
            action__in=NON_PURGEABLE_ACTIONS
        )

        if options["dry_run"]:
            count = old_qs.count()
            logger.info(
                "purge_audit_log dry-run: %d row(s) older than %dd (cutoff %s) would be deleted "
                "(non-purgeable actions %s excluded regardless of age).",
                count,
                days,
                cutoff.isoformat(),
                NON_PURGEABLE_ACTIONS,
            )
            self.stdout.write(
                f"[dry-run] would delete {count} audit row(s) older than {days} days "
                f"(excludes non-purgeable actions: {', '.join(NON_PURGEABLE_ACTIONS)})."
            )
            return

        total = 0
        while True:
            ids = list(old_qs.values_list("pk", flat=True)[:batch_size])
            if not ids:
                break
            with transaction.atomic():
                AuditLogEntry.objects.filter(pk__in=ids).delete()
            total += len(ids)

        logger.info(
            "purge_audit_log: deleted %d audit row(s) older than %dd (cutoff %s).",
            total,
            days,
            cutoff.isoformat(),
        )
        self.stdout.write(f"Deleted {total} audit row(s) older than {days} days.")
