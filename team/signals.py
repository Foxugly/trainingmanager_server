"""Signals that keep event.members in sync with TeamMembership state.

Strategy:
- A new active TeamMembership (left_at IS NULL) -> attach the member
  to all FUTURE events of the team.
- A TeamMembership update where left_at becomes non-NULL -> remove the
  member from all future events of the team.

"Future" is computed against `Event.date` which is a DateField (not
DateTimeField). We compare against `timezone.localdate()`. Events with
date IS NULL are skipped — without a date we cannot say "future" honestly.

Past events are never touched: attendance history is preserved.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Team, TeamMembership

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TeamMembership)
def sync_event_members_on_membership_change(sender, instance, created, **kwargs):
    from event.models import Event

    today = timezone.localdate()
    team = instance.team
    member = instance.member

    attach = created and instance.left_at is None
    detach = not created and instance.left_at is not None
    if not (attach or detach):
        return

    # One query for the id list, then a single M2M add/remove from the member
    # side (member.event_set is Event.members reversed) — not one write per
    # event.
    future_event_ids = list(
        Event.objects.filter(refer_program__team=team, date__gte=today).values_list(
            "id", flat=True
        )
    )
    if not future_event_ids:
        return

    if attach:
        member.event_set.add(*future_event_ids)
        logger.info(
            "Auto-attached member %s to %d future events of team %s (membership created)",
            member.pk,
            len(future_event_ids),
            team.pk,
        )
    else:
        member.event_set.remove(*future_event_ids)
        logger.info(
            "Auto-detached member %s from %d future events of team %s (membership ended at %s)",
            member.pk,
            len(future_event_ids),
            team.pk,
            instance.left_at,
        )


@receiver(post_save, sender=Team)
def attach_default_attendance_statuses_on_create(sender, instance, created, **kwargs):
    """Auto-attach default AttendanceStatuses to a newly created team.

    Only fires on creation. Skips if the team already has any
    attendance_statuses (e.g. attached pre-save by a fixture or
    serializer) to avoid clobbering caller intent.
    """
    if not created:
        return

    if instance.attendance_statuses.exists():
        return

    from attendance.models import AttendanceStatus

    defaults = AttendanceStatus.objects.filter(is_default=True, is_active=True)
    if defaults.exists():
        instance.attendance_statuses.set(defaults)
        logger.info(
            "Auto-attached %d default attendance status(es) to new team %s",
            defaults.count(),
            instance.pk,
        )
