from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationType(models.TextChoices):
    """The kind of event a notification reports.

    The enum is deliberately open-ended: the messaging feature will add
    ``message_*`` members later. Channel preferences and the service layer
    iterate over ``NotificationType.choices`` / ``.values`` so a new member
    is picked up automatically (defaults in_app=True, email=True) without a
    data migration.
    """

    NOTE_FOR_COACH = "note_for_coach", _("Note added on an athlete")
    NOTE_FOR_ATHLETE = "note_for_athlete", _("A note was shared with you")
    MESSAGE_NEW_TOPIC = "message_new_topic", _("A new topic was created in your team")
    MESSAGE_NEW_REPLY = "message_new_reply", _("A new message was posted in a topic")
    PERFORMANCE_LOGGED = "performance_logged", _("A performance was logged for you")
    PB_BEATEN = "pb_beaten", _("You beat a personal best")
    PLAN_GENERATED = "plan_generated", _("A new training plan was scheduled")
    SESSION_REMINDER = "session_reminder", _("You have a session tomorrow")


class Notification(models.Model):
    """A single in-app notification addressed to one recipient.

    Rows are created by ``notifications.services.notify`` only when the
    recipient's in-app channel is enabled for the notification's type. The
    ``url`` is a frontend deep-link PATH (e.g. ``/teams/3``); the absolute
    URL for emails is built by prefixing ``FRONTEND_BASE_URL`` at send time.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(choices=NotificationType.choices, max_length=40)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    url = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text=_("Frontend deep-link path, e.g. /teams/3."),
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.type} -> {self.recipient} ({'read' if self.is_read else 'unread'})"


class NotificationPreference(models.Model):
    """Per-user, per-type channel preference.

    Absence of a row means "use the defaults" (in_app=True, email=True);
    the service layer never relies on rows existing. A unique constraint on
    (user, type) keeps the matrix one-row-per-cell.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    type = models.CharField(choices=NotificationType.choices, max_length=40)
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=True)
    push = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "type"], name="uniq_notifpref_user_type"),
        ]

    def __str__(self):
        return f"{self.user} / {self.type} (in_app={self.in_app}, email={self.email}, push={self.push})"
