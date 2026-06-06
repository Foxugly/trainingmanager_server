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
    def create_user(self, username, email, password=None, **extra_fields):
        """Create and save a user with the given username, email and password.

        Accepts any AbstractUser/CustomUser field as keyword argument
        (first_name, last_name, language, is_staff, ...). Mirrors Django's
        standard UserManager.create_user contract.
        """
        if not username:
            raise ValueError("Username is required.")
        if not email:
            raise ValueError("Email is required.")

        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_active", True)

        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        """Create and save a superuser. is_staff and is_superuser are forced to True."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
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
    team_quota = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Maximum number of active teams this user can own. Defaults to 0 — "
            "creating a team is a paid feature; admins bump this per user (or "
            "via a future billing flow)."
        ),
    )
    calendar_token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=generate_calendar_token,
        help_text=_(
            "Unguessable token embedded in the user's personal iCal (.ics) "
            "subscription URL. The token IS the authentication for that "
            "(otherwise anonymous) feed. Rotate it to revoke an old URL."
        ),
    )
    objects = CustomUserManager()

    USERNAME_FIELD = "username"

    def __str__(self):
        return self.username

    def active_owned_teams_count(self) -> int:
        """Number of teams the user currently owns AND that are still active.
        Soft-deleted teams (is_active=False) free up a slot."""
        return self.owned_teams.filter(is_active=True).count()

    def can_create_team(self) -> bool:
        return self.active_owned_teams_count() < self.team_quota

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
