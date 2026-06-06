from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditAction(models.TextChoices):
    """Stable codes for security-sensitive actions recorded in the audit log.

    Codes are persisted verbatim; NEVER rename an existing value (the string
    is the contract). Add new members for new audited actions.
    """

    MEMBER_ANONYMIZED = "member_anonymized", _("Member anonymized")
    MEMBER_REMOVED = "member_removed", _("Member removed from team")
    ACCOUNT_DELETED = "account_deleted", _("Account deleted")
    SESSION_SHARED = "session_shared", _("Session shared publicly")
    SESSION_UNSHARED = "session_unshared", _("Session sharing disabled")
    ATTACHMENT_DELETED = "attachment_deleted", _("Attachment deleted")
    TEAM_CONFIG_UPDATED = "team_config_updated", _("Team configuration updated")


class AuditLogEntry(models.Model):
    """One immutable record of a security-sensitive action.

    Records who (actor + actor_label snapshot) did what (action) to what
    (target_repr + metadata), when (created_at), scoped to a team where
    applicable so team owners/managers can review their team's log.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        help_text=_("The user who performed the action. NULL = system/anonymous."),
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text=_(
            "Snapshot of the actor's username/display at the time. Survives "
            "actor deletion or anonymization (when the FK is SET_NULL)."
        ),
    )
    action = models.CharField(
        max_length=50,
        choices=AuditAction.choices,
        help_text=_("Stable code identifying the audited action."),
    )
    team = models.ForeignKey(
        "team.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        help_text=_(
            "Team the action is scoped to (used for read scoping). NULL = not "
            "team-scoped, e.g. an account self-delete."
        ),
    )
    target_repr = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Human-readable description of the target."),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Small structured extra context (no unnecessary PII)."),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["team", "created_at"], name="audit_team_created_idx"),
        ]
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log entries")

    def __str__(self):
        who = self.actor_label or "system"
        return f"{who} {self.action} ({self.created_at:%Y-%m-%d %H:%M})"
