"""Send the weekly recap email to coaches of teams that have it enabled.

Recap window: the last COMPLETED ISO week (Monday..Sunday) relative to today,
overridable via --week-offset or explicit --from/--to for testing/manual runs.

For each Team with weekly_recap_enabled=True we compute, over the recap week:
  - number of sessions (events with a date in the window),
  - team attendance rate (present rows / (active members * sessions)),
  - total volume (sum of Event.total),
and the count of upcoming sessions in the NEXT week (the 7 days after the
window). The aggregation mirrors team.views.TeamViewSet stats helpers:
events come from Event.refer_program__team, "present" = Attendance whose
status.code == "present", volume = Event.total.

Recipients = the team's owner + managers who have weekly_recap_opt_in=True and
a non-empty email. Recaps are AGGREGATED per recipient: a coach managing
several enabled teams gets ONE email listing each team's recap. The email is
localized to each recipient's language.

Idempotent: a ``WeeklyRecapLog(user, week_start)`` marker is written after each
successful send, so re-running the command in the same week skips already-sent
recipients instead of double-sending (a failed send is NOT marked, so it retries
next run). --dry-run computes + logs without sending or marking.
"""

import datetime
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone, translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


def last_completed_week(today, offset=0):
    """Return (monday, sunday) of the last completed ISO week relative to today.

    offset shifts further into the past in whole weeks (offset=1 -> the week
    before the last completed one). The "last completed week" is the Mon..Sun
    block that ended on or before yesterday: we take this week's Monday and
    step back 7 days (plus 7*offset).
    """
    this_monday = today - datetime.timedelta(days=today.weekday())
    monday = this_monday - datetime.timedelta(days=7 * (offset + 1))
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def compute_team_recap(team, date_from, date_to):
    """Compute the recap dict for a single team over [date_from, date_to].

    Returns a dict with: sessions, attendance_rate (0..1 or None), total_volume,
    upcoming_next_week. Reuses the same query shape as the stats endpoint.
    """
    from attendance.models import Attendance
    from event.models import Event

    events = list(
        Event.objects.filter(
            refer_program__team=team,
            date__isnull=False,
            date__gte=date_from,
            date__lte=date_to,
        ).values("id", "total")
    )
    event_ids = [e["id"] for e in events]
    sessions = len(events)
    total_volume = sum(e["total"] for e in events)

    active_member_count = team.memberships.filter(left_at__isnull=True).count()

    if event_ids and active_member_count:
        present = Attendance.objects.filter(
            event_id__in=event_ids, status__code="present"
        ).count()
        expected = active_member_count * sessions
        attendance_rate = (present / expected) if expected else None
    else:
        attendance_rate = None

    next_from = date_to + datetime.timedelta(days=1)
    next_to = next_from + datetime.timedelta(days=6)
    upcoming_next_week = Event.objects.filter(
        refer_program__team=team,
        date__isnull=False,
        date__gte=next_from,
        date__lte=next_to,
    ).count()

    return {
        "team": team,
        "sessions": sessions,
        "attendance_rate": attendance_rate,
        "total_volume": total_volume,
        "upcoming_next_week": upcoming_next_week,
    }


def build_email_body(recipient, recaps, date_from, date_to):
    """Render the localized plain-text recap body for one recipient.

    `recaps` is the list of per-team recap dicts for the teams this recipient
    coaches. Must be called inside a translation.override block.
    """
    greeting_name = recipient.first_name or recipient.username
    lines = [
        f"{_('Hello')} {greeting_name},",
        "",
        _("Here is your weekly TrainingManager recap for the week of %(from)s to %(to)s.")
        % {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "",
    ]
    for recap in recaps:
        team = recap["team"]
        if recap["attendance_rate"] is None:
            rate_str = _("n/a")
        else:
            rate_str = f"{round(recap['attendance_rate'] * 100)}%"
        lines.append(f"== {team.name} ==")
        lines.append(f"  {_('Sessions')}: {recap['sessions']}")
        lines.append(f"  {_('Attendance rate')}: {rate_str}")
        lines.append(f"  {_('Total volume')}: {recap['total_volume']}")
        lines.append(
            f"  {_('Upcoming sessions next week')}: {recap['upcoming_next_week']}"
        )
        lines.append("")

    lines.append(
        _("You receive this because you own or manage these teams. To stop, "
          "disable the weekly recap in your account preferences.")
    )
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Send the weekly recap email to coaches of teams with weekly_recap_enabled=True."

    def add_arguments(self, parser):
        parser.add_argument(
            "--week-offset",
            type=int,
            default=0,
            help=(
                "Shift the recap window further into the past, in whole weeks. "
                "0 (default) = last completed week. Ignored if --from/--to given."
            ),
        )
        parser.add_argument(
            "--from",
            dest="from_date",
            type=str,
            default=None,
            help="Explicit window start (ISO YYYY-MM-DD). Requires --to.",
        )
        parser.add_argument(
            "--to",
            dest="to_date",
            type=str,
            default=None,
            help="Explicit window end (ISO YYYY-MM-DD). Requires --from.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and log the recaps but do not send any email.",
        )

    def _resolve_window(self, options):
        raw_from = options.get("from_date")
        raw_to = options.get("to_date")
        if raw_from or raw_to:
            if not (raw_from and raw_to):
                raise CommandError("--from and --to must be provided together.")
            try:
                date_from = datetime.date.fromisoformat(raw_from)
                date_to = datetime.date.fromisoformat(raw_to)
            except ValueError as exc:
                raise CommandError(f"Invalid date: {exc}") from exc
            if date_from > date_to:
                raise CommandError("--from must be on or before --to.")
            return date_from, date_to
        today = timezone.localdate()
        return last_completed_week(today, offset=options["week_offset"])

    def handle(self, *args, **options):
        from customuser.models import WeeklyRecapLog
        from team.models import Team

        dry_run = options["dry_run"]
        date_from, date_to = self._resolve_window(options)
        logger.info(
            "Weekly recap window: %s .. %s (dry_run=%s)",
            date_from,
            date_to,
            dry_run,
        )

        teams = (
            Team.objects.filter(weekly_recap_enabled=True, is_active=True)
            .select_related("owner")
            .prefetch_related("managers")
        )

        # Aggregate recaps per recipient. Map user -> list of team recap dicts,
        # deduping the owner/manager overlap on user id.
        recaps_by_user: dict[int, dict] = {}

        for team in teams:
            recap = compute_team_recap(team, date_from, date_to)

            candidates = list(team.managers.all()) + [team.owner]
            seen_for_team: set[int] = set()
            for user in candidates:
                if user.pk in seen_for_team:
                    continue
                seen_for_team.add(user.pk)
                if not user.weekly_recap_opt_in:
                    continue
                if not user.email:
                    continue
                entry = recaps_by_user.setdefault(
                    user.pk, {"user": user, "recaps": []}
                )
                entry["recaps"].append(recap)

        sent = 0
        skipped = 0
        for entry in recaps_by_user.values():
            user = entry["user"]
            recaps = entry["recaps"]
            if not recaps:  # defensive: skip recipients with no enabled teams
                continue

            # Idempotency: once a recap for this (user, week) was sent, don't
            # re-send on a same-week re-run. Checked before building the email.
            if not dry_run and WeeklyRecapLog.objects.filter(
                user=user, week_start=date_from
            ).exists():
                skipped += 1
                continue

            lang = user.language or "en"
            with translation.override(lang):
                subject = f"[TrainingManager] {_('Votre récap hebdo')}"
                body = build_email_body(user, recaps, date_from, date_to)

            if dry_run:
                logger.info(
                    "[dry-run] would email %s (%d team(s))", user.email, len(recaps)
                )
                continue

            try:
                send_mail(
                    subject=str(subject),
                    message=str(body),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                # Mark sent only after a successful delivery, so a failed send
                # is retried on the next run rather than silently skipped.
                WeeklyRecapLog.objects.get_or_create(user=user, week_start=date_from)
                sent += 1
            except Exception:
                logger.exception("Failed to send weekly recap to %s", user.email)

        if dry_run:
            logger.info(
                "Weekly recap dry-run complete: %d recipient(s) would be emailed.",
                len(recaps_by_user),
            )
        else:
            logger.info(
                "Weekly recap complete: %d email(s) sent, %d already-sent skipped.",
                sent,
                skipped,
            )
