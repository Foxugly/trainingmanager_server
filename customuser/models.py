import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


def generate_calendar_token():
    """Generate a fresh, URL-safe token for a user's iCal feed.

    The token IS the authentication for the unauthenticated .ics feed
    endpoint, so it must be unguessable. secrets.token_urlsafe(32) yields
    ~43 chars of base64url (256 bits of entropy).
    """
    return secrets.token_urlsafe(32)


class CustomUserManager(BaseUserManager):
    """Email-only user manager (no username).

    ``email`` is the ``USERNAME_FIELD`` — there is no ``username`` column
    anymore. Mirrors Django's stock ``UserManager`` contract but keyed on
    email instead of username (fleet canonical pattern, OPERATIONS.md §3.16).
    """

    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        """Create and save a user with the given email and password.

        Accepts any AbstractUser/CustomUser field as keyword argument
        (first_name, last_name, language, is_staff, ...).
        """
        if not email:
            raise ValueError("Email is required.")

        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_active", True)

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser. is_staff and is_superuser are forced to True."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    # Email-only auth: drop username entirely, key on email (fleet canonical
    # pattern, OPERATIONS.md §3.16). USERNAME_FIELD = "email" below.
    username = None
    email = models.EmailField(_("email address"), unique=True)
    email_confirmed = models.BooleanField(
        default=False,
        help_text=_(
            "True once the user has proven control of their email address "
            "(registration confirmation, password reset, or a verified email "
            "change). The login + magic-link gates require this. Replaces the "
            "former allauth EmailAddress.verified flag."
        ),
    )
    language = models.CharField(
        _("language"),
        max_length=2,
        choices=settings.LANGUAGES,
        default="fr",
    )
    weekly_recap_opt_in = models.BooleanField(
        default=True,
        help_text=_(
            "Per-user preference for the weekly recap email. Opt-out model: "
            "default True. When False, this user receives no weekly recap even "
            "for teams they own/manage that have it enabled."
        ),
    )
    digest_email = models.BooleanField(
        default=False,
        help_text=_(
            "When True, suppress immediate notification emails and instead send a "
            "single daily digest of the day's notifications. Opt-in (default False)."
        ),
    )
    last_digest_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the last notification digest was sent to this user."),
    )
    team_quota = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Maximum number of active teams this user can own. Defaults to 0 — "
            "creating a team is a paid feature; admins bump this per user (or "
            "via a future billing flow)."
        ),
    )
    subscription_bypass = models.BooleanField(
        default=False,
        help_text=_(
            "When True, grants every paid feature without a subscription "
            "(offered access): the team quota becomes unlimited. Distinct from "
            "is_staff, which grants no business entitlement."
        ),
    )
    bypass_note = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Audit only: why this account was offered access."),
    )
    bypass_granted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Audit only: when the access was first granted."),
    )
    calendar_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_calendar_token,
        help_text=_(
            "Unguessable token embedded in the user's personal iCal (.ics) "
            "subscription URL. The token IS the authentication for that "
            "(otherwise anonymous) feed. Rotate it to revoke an old URL."
        ),
    )
    magic_link_nonce = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Single-use nonce for the current passwordless magic-login link. "
            "Set when a link is requested, baked into the signed token, and "
            "cleared (consumed) on exchange — so a link works at most once and "
            "a new request invalidates the previous one. NULL = no link pending."
        ),
    )
    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    def active_owned_teams_count(self) -> int:
        """Number of teams the user currently owns AND that are still active.
        Soft-deleted teams (is_active=False) free up a slot."""
        return self.owned_teams.filter(is_active=True).count()

    def can_create_team(self) -> bool:
        from customuser.entitlements import can_create_team

        return can_create_team(self)

    def rotate_calendar_token(self) -> str:
        """Generate a new calendar_token, persist it, and return it.

        Rotating immediately invalidates the previous .ics subscription URL
        (the old token no longer matches any user). Loops on the (extremely
        unlikely) unique-constraint collision so a caller always gets a live
        token back."""
        from django.db import IntegrityError

        for _attempt in range(5):
            self.calendar_token = generate_calendar_token()
            try:
                self.save(update_fields=["calendar_token"])
            except IntegrityError:
                continue
            return self.calendar_token
        # Practically unreachable (256-bit collision five times in a row).
        raise RuntimeError("Could not generate a unique calendar_token.")


class WeeklyRecapLog(models.Model):
    """Idempotency marker for the weekly-recap email.

    One row per (recipient, recap week-start) once the email has been sent, so
    re-running ``send_weekly_recaps`` in the same week is a no-op instead of a
    double-send. The unique constraint also makes concurrent runs safe.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="weekly_recap_logs",
    )
    week_start = models.DateField(help_text=_("Monday of the recap week."))
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "week_start"], name="uniq_weekly_recap_user_week"
            ),
        ]

    def __str__(self):
        return f"recap {self.user_id} / {self.week_start}"
