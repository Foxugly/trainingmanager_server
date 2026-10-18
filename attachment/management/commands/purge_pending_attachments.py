"""Delete attachment uploads that were never completed.

``presign`` creates a ``pending`` Attachment row and mints a PUT URL; ``complete``
flips it to ``ready``. An upload that is abandoned (tab closed, network lost,
client that never calls ``complete``) leaves the row and possibly the S3 object
behind forever: nothing lists ``pending`` rows and the bucket lifecycle rule only
covers incomplete multipart uploads.

This command deletes every ``pending`` row older than ``--hours`` (default 24,
far past the presigned URL expiry) together with its S3 object (best effort: the
row is dropped even if the S3 delete fails, and the failure is logged).

Idempotent; ``--dry-run`` counts without deleting. Operator-facing output only.
Intended to run from a daily systemd timer (deploy/systemd/tm-attachment-purge.*).
"""

import datetime
import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attachment import s3
from attachment.models import Attachment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete pending (never completed) attachment uploads older than --hours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Delete pending uploads older than this many hours (default 24).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be deleted; delete nothing.",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours < 1:
            raise CommandError("--hours must be >= 1.")
        cutoff = timezone.now() - datetime.timedelta(hours=hours)
        stale = Attachment.objects.filter(status=Attachment.PENDING, created_at__lt=cutoff)

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] would delete {stale.count()} pending upload(s) older than {hours}h."
            )
            return

        configured = s3.attachments_configured()
        total = 0
        for attachment in stale.iterator():
            if configured:
                try:
                    s3.delete_object(attachment.s3_key)
                except Exception:  # noqa: BLE001 - best-effort, the row goes anyway
                    logger.exception(
                        "S3 delete failed for stale pending attachment %s", attachment.pk
                    )
            attachment.delete()
            total += 1

        logger.info("purge_pending_attachments: deleted %d pending upload(s).", total)
        self.stdout.write(f"Deleted {total} pending upload(s) older than {hours}h.")
