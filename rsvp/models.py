from django.db import models
from django.utils.translation import gettext_lazy as _


class RsvpStatus(models.TextChoices):
    GOING = "going", _("Going")
    MAYBE = "maybe", _("Maybe")
    NOT_GOING = "not_going", _("Not going")


class Rsvp(models.Model):
    """A single athlete's availability (RSVP) for an event.

    The athlete declares whether they are going / maybe / not going to a
    session. One row per (event, member); the athlete upserts their own
    status. Mirrors the Roti / Attendance models (event + member FKs,
    unique-together), gated by the team's `rsvp_enabled` toggle.
    """

    event = models.ForeignKey(
        "event.Event",
        on_delete=models.CASCADE,
        related_name="rsvps",
    )
    member = models.ForeignKey(
        "member.Member",
        on_delete=models.CASCADE,
        related_name="rsvps",
    )
    status = models.CharField(
        max_length=10,
        choices=RsvpStatus.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "member"],
                name="uniq_rsvp_event_member",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "member"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.member} @ {self.event}: RSVP {self.status}"
