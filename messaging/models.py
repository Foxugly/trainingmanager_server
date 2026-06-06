from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TopicAudience(models.TextChoices):
    """Who can see a topic and (for team topics) who can reply.

    - ``team``: every team member (owner, managers, active athlete members)
      can see the topic. Athletes can also post when
      ``allow_athlete_replies`` is True.
    - ``coaches``: only the team owner and managers can see the topic — a
      staff-only channel that is never leaked to athletes.
    """

    TEAM = "team", _("Whole team")
    COACHES = "coaches", _("Coaches only")


class Topic(models.Model):
    """A discussion topic scoped to a team.

    Topics are created by coaches (team owner/managers). Visibility and the
    right to reply depend on ``audience`` and ``allow_athlete_replies`` —
    see ``messaging.permissions`` and the viewset querysets.

    ``updated_at`` is bumped each time a new message is posted so the topic
    list can be ordered by most-recent activity.
    """

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="topics",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="topics_authored",
        help_text=_("The coach who created the topic. SET_NULL on user deletion."),
    )
    title = models.CharField(max_length=200)
    audience = models.CharField(
        max_length=10,
        choices=TopicAudience.choices,
        default=TopicAudience.TEAM,
        help_text=_(
            "team = visible to all team members; coaches = visible only to the "
            "owner and managers (staff channel)."
        ),
    )
    allow_athlete_replies = models.BooleanField(
        default=True,
        help_text=_(
            "Only meaningful when audience=team. When True, athlete members may "
            "post messages; coaches can always post."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text=_("Bumped on each new message for activity-based ordering."),
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["team", "audience", "-updated_at"],
                name="topic_team_audience_idx",
            ),
        ]

    def __str__(self):
        author_str = self.author.username if self.author else "(deleted)"
        return f"Topic #{self.pk} '{self.title}' by {author_str} [{self.audience}]"


class Message(models.Model):
    """A single flat message inside a topic (no nested replies)."""

    topic = models.ForeignKey(
        Topic,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="topic_messages_authored",
        help_text=_("Author of the message. SET_NULL on user deletion."),
    )
    content = models.TextField(
        help_text=_("Rich HTML content (sanitized via bleach on save)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["topic", "created_at"],
                name="message_topic_created_idx",
            ),
        ]

    def __str__(self):
        author_str = self.author.username if self.author else "(deleted)"
        return f"Message #{self.pk} in topic {self.topic_id} by {author_str}"
