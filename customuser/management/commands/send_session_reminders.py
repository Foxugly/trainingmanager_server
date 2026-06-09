"""Send a daily "session tomorrow" reminder to active athletes.

For every Event scheduled for TOMORROW (relative to today's local date), each
ACTIVE athlete of the event's team who has a linked user account is notified
via the notifications service (type=SESSION_REMINDER). The notification fans
out to the athlete's enabled channels (in-app row + localized email) exactly
like the other triggers.

Pragmatic date model: "tomorrow" is ``timezone.localdate() + 1 day`` (a single
server-local date), not per-team timezone. This keeps the daily cron simple and
is good enough for a morning reminder.

De-duplication: within a single run, a given (user, event) pair is notified at
most once even if the same athlete belongs to the event's team via several
membership rows. The command is safe to run daily; it does NOT keep a persistent
"already reminded" table, so re-running it on the same day would notify again —
the systemd timer fires it once per day, which is the intended cadence.

--dry-run computes and logs the reminders without creating notifications.
"""

import datetime
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Notify active athletes of sessions happening tomorrow (daily cron)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and log the reminders but do not create any notification.",
        )

    def handle(self, *args, **options):
        from event.models import Event
        from member.models import Member
        from notifications.models import NotificationType
        from notifications.services import notify_many

        dry_run = options["dry_run"]
        target_date = timezone.localdate() + datetime.timedelta(days=1)
        logger.info(
            "Session reminders for %s (dry_run=%s)", target_date, dry_run
        )

        events = (
            Event.objects.filter(date=target_date, refer_program__isnull=False)
            .select_related("refer_program", "refer_program__team")
        )

        notified = 0
        for event in events:
            team = event.team
            if team is None:
                continue

            # Active athletes of the event's team that have a linked user.
            members = (
                Member.objects.filter(
                    memberships__team=team,
                    memberships__left_at__isnull=True,
                    user__isnull=False,
                )
                .select_related("user")
                .distinct()
            )

            # De-dupe (user, event): distinct() above guards membership dupes,
            # but keep an explicit per-event seen set for safety.
            seen_user_ids: set[int] = set()
            recipients = []
            for member in members:
                user = member.user
                if user.pk in seen_user_ids:
                    continue
                seen_user_ids.add(user.pk)
                recipients.append(user)

            hour = event.hour_start.strftime("%H:%M") if event.hour_start else _("TBD")
            location = event.location or _("TBD")

            if dry_run:
                for user in recipients:
                    logger.info(
                        "[dry-run] would remind %s of event %s", user.pk, event.pk
                    )
                continue

            try:
                notify_many(
                    recipients,
                    NotificationType.SESSION_REMINDER,
                    title=_("Session tomorrow"),
                    body=_("%(name)s at %(hour)s — %(location)s")
                    % {
                        "name": event.name,
                        "hour": hour,
                        "location": location,
                    },
                    url=f"/events/{event.pk}",
                    actor=None,
                )
                notified += len(recipients)
            except Exception:  # pragma: no cover - defensive per event
                logger.exception(
                    "Failed to send session reminders for event %s",
                    event.pk,
                )

        if dry_run:
            logger.info("Session reminders dry-run complete for %s.", target_date)
        else:
            logger.info(
                "Session reminders complete: %d reminder(s) sent for %s.",
                notified,
                target_date,
            )
