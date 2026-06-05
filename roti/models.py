from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Roti(models.Model):
    """A single athlete's session-difficulty rating (ROTI) for an event.

    ROTI = "Return On Time Invested": the athlete scores how hard the
    session felt on a 1..5 scale. One row per (event, member); the athlete
    upserts their own score. Mirrors the Attendance model (event + member
    FKs, unique-together).
    """

    event = models.ForeignKey(
        "event.Event",
        on_delete=models.CASCADE,
        related_name="rotis",
    )
    member = models.ForeignKey(
        "member.Member",
        on_delete=models.CASCADE,
        related_name="rotis",
    )
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "member"],
                name="uniq_roti_event_member",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "member"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.member} @ {self.event}: ROTI {self.score}"
