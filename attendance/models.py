from django.db import models
from django.utils.translation import gettext_lazy as _


class AttendanceStatus(models.Model):
    """Status of an athlete's attendance at an event.

    Catalog managed by admins. Each team activates a subset via
    Team.attendance_statuses M2M.
    """

    code = models.CharField(
        max_length=30,
        unique=True,
        help_text=_("Snake_case identifier (e.g. 'present', 'absent', 'excused')"),
    )
    label = models.CharField(
        max_length=100,
        help_text=_("Display label, translated via modeltranslation."),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("If True, pre-selected as default in the frontend UI."),
    )
    order = models.IntegerField(
        default=0,
        help_text=_("Display order for buttons in the frontend."),
    )
    color = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Hex color (#xxxxxx) for the frontend, optional."),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "code"]

    def __str__(self):
        return f"{self.code} ({self.label})"


class Attendance(models.Model):
    """Records the attendance status of a member at a specific event."""

    event = models.ForeignKey(
        "event.Event",
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    member = models.ForeignKey(
        "member.Member",
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    status = models.ForeignKey(
        AttendanceStatus,
        on_delete=models.PROTECT,
        related_name="attendances",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "member"],
                name="unique_attendance_per_event_member",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "member"]),
            models.Index(fields=["member", "-created_at"]),
            # The stats / weekly-recap / dashboard hot path filters
            # status_id (the team's "present") over a set of event_ids; this
            # composite lets that predicate use an index instead of scanning.
            models.Index(fields=["status", "event"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.member} @ {self.event}: {self.status.code}"
