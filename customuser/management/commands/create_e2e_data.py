"""Seed a deterministic, known dataset for the Playwright e2e suite.

This command provisions exactly the entities the critical-path specs need
(login -> create team -> create event -> RSVP) using stable natural keys so
it is fully idempotent: running it repeatedly never errors and never creates
duplicates. Re-runs reset the seeded users' passwords and re-assert the
verified-email + active-membership state so a flaky local DB can be healed by
simply running the command again.

GUARD: this command writes test fixtures (well-known credentials, a verified
manager account) and must NEVER touch a production database. It refuses to run
when settings look like production (DEBUG off AND STATE in {PROD, PRODUCTION}).

The exact values seeded (consumed by the Playwright specs) are defined as
module-level constants below; keep them in sync with the e2e config.
"""

import logging

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django.utils import timezone

from event.models import Event
from exercise.models import EnergySegment, EnergySystem, Modality
from member.models import Member
from program.models import Program
from sport.models import Sport
from team.models import Team, TeamMembership, TeamSport

logger = logging.getLogger(__name__)

# --- Seeded values (the Playwright specs depend on these exact strings) ------
E2E_PASSWORD = "e2e-Passw0rd!"

MANAGER_USERNAME = "e2e-manager@foxugly.com"
MANAGER_EMAIL = "e2e-manager@foxugly.com"

ATHLETE_USERNAME = "e2e-athlete@foxugly.com"
ATHLETE_EMAIL = "e2e-athlete@foxugly.com"

SPORT_NAME = "E2E Sport"
SPORT_SLUG = "e2e-sport"

ENERGY_SYSTEM_NAME = "E2E ES"
MODALITY_NAME = "E2E Modality"
ENERGY_SEGMENT_ABV = "e2e-seg"

TEAM_NAME = "E2E Team"
PROGRAM_NAME = "E2E Program"
EVENT_NAME = "E2E Session"

# Seeded users render the SPA in English so the Playwright specs' English
# accessible-name selectors are deterministic (no per-locale label drift).
SEED_LANGUAGE = "en"

ATHLETE_FIRSTNAME = "E2E"
ATHLETE_LASTNAME = "Athlete"


class Command(BaseCommand):
    help = (
        "Seed a deterministic, idempotent dataset for the Playwright e2e suite "
        "(manager + athlete users, a sport, a team, a program, an active "
        "membership). Guarded: refuses to run against a production database."
    )

    def _ensure_not_production(self):
        """Refuse to run when settings look like production.

        Production runs with DEBUG=False and STATE in {PROD, PRODUCTION}.
        Either DEBUG being on OR a non-prod STATE is enough to clear the
        guard, so int/dev/test/local all pass while the box's prod settings
        hard-fail.
        """
        state = str(getattr(settings, "STATE", "")).strip().upper()
        if not settings.DEBUG and state in ("PROD", "PRODUCTION"):
            raise CommandError(
                "Refusing to seed e2e data: this looks like a production "
                f"environment (DEBUG=False, STATE={state!r})."
            )

    def _ensure_verified_user(self, *, username, email, first_name=""):
        """get_or_create a user keyed on username; always (re)assert the
        e2e password, active flag, and a verified primary EmailAddress so the
        login gate (VerifiedTokenObtainPairSerializer) lets them through."""
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": first_name,
                "is_active": True,
                "language": SEED_LANGUAGE,
            },
        )
        # Re-assert mutable state on every run (idempotent self-heal).
        user.email = email
        user.first_name = first_name
        user.is_active = True
        user.language = SEED_LANGUAGE
        user.set_password(E2E_PASSWORD)
        user.save()

        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )
        return user, created

    @transaction.atomic
    def handle(self, *args, **options):
        self._ensure_not_production()

        # --- Manager user ---------------------------------------------------
        manager, manager_created = self._ensure_verified_user(
            username=MANAGER_USERNAME, email=MANAGER_EMAIL, first_name="E2E Manager"
        )
        # A team is a paid feature gated on team_quota; give the manager room
        # so the create-team flow works through the UI/API too.
        if manager.team_quota < 5:
            manager.team_quota = 5
            manager.save(update_fields=["team_quota"])

        # --- Sport + structured-content referentials ------------------------
        sport, sport_created = Sport.objects.update_or_create(
            slug=SPORT_SLUG,
            defaults={"name": SPORT_NAME, "is_active": True},
        )
        energy_system, _es_created = EnergySystem.objects.get_or_create(
            name=ENERGY_SYSTEM_NAME, defaults={"is_active": True}
        )
        sport.energy_systems.add(energy_system)

        modality, _mod_created = Modality.objects.get_or_create(
            name=MODALITY_NAME, sport=sport, defaults={"is_active": True}
        )
        energy_segment, _seg_created = EnergySegment.objects.get_or_create(
            abv=ENERGY_SEGMENT_ABV,
            energysystem=energy_system,
            defaults={"description": "E2E energy segment", "is_active": True},
        )

        # --- Team (owned by manager, default sport, RSVP on) ----------------
        team, team_created = Team.objects.update_or_create(
            name=TEAM_NAME,
            defaults={
                "owner": manager,
                "language": "fr",
                "is_active": True,
                "rsvp_enabled": True,
            },
        )
        # Default TeamSport link (idempotent: one default sport per team).
        team_sport, _ts_created = TeamSport.objects.update_or_create(
            team=team,
            sport=sport,
            defaults={"is_default": True, "order": 0},
        )

        # --- Program under the team -----------------------------------------
        program, program_created = Program.objects.update_or_create(
            name=PROGRAM_NAME,
            team=team,
            defaults={"is_active": True},
        )

        # --- Event under the program (today, so it shows in the current-month
        # calendar grid the athlete opens for the RSVP flow) ------------------
        today = timezone.now().date()
        event, event_created = Event.objects.update_or_create(
            name=EVENT_NAME,
            refer_program=program,
            defaults={"date": today, "sport": sport},
        )

        # --- Athlete user + Member + active membership ----------------------
        athlete, athlete_created = self._ensure_verified_user(
            username=ATHLETE_USERNAME, email=ATHLETE_EMAIL, first_name="E2E Athlete"
        )
        member, _member_created = Member.objects.get_or_create(
            user=athlete,
            defaults={
                "firstname": ATHLETE_FIRSTNAME,
                "lastname": ATHLETE_LASTNAME,
                "email": ATHLETE_EMAIL,
            },
        )
        # Active membership = exactly one row with left_at IS NULL for this
        # (team, member). Re-create only if no active one exists.
        membership = TeamMembership.objects.filter(
            team=team, member=member, left_at__isnull=True
        ).first()
        membership_created = False
        if membership is None:
            membership = TeamMembership.objects.create(team=team, member=member)
            membership_created = True

        # --- Summary --------------------------------------------------------
        def mark(created):
            return "created" if created else "exists"

        summary = [
            "e2e seed complete:",
            f"  manager  : id={manager.id} {MANAGER_EMAIL} ({mark(manager_created)})",
            f"  athlete  : id={athlete.id} {ATHLETE_EMAIL} ({mark(athlete_created)})",
            f"  password : {E2E_PASSWORD} (both users)",
            f"  sport    : id={sport.id} slug={SPORT_SLUG} ({mark(sport_created)})",
            f"  modality : id={modality.id} energy_system id={energy_system.id} "
            f"energy_segment id={energy_segment.id}",
            f"  team     : id={team.id} '{TEAM_NAME}' rsvp_enabled={team.rsvp_enabled} "
            f"({mark(team_created)})",
            f"  program  : id={program.id} '{PROGRAM_NAME}' ({mark(program_created)})",
            f"  event    : id={event.id} '{EVENT_NAME}' date={event.date} ({mark(event_created)})",
            f"  member   : id={member.id} membership id={membership.id} "
            f"active=True ({mark(membership_created)})",
        ]
        self.stdout.write("\n".join(summary))
        logger.info("create_e2e_data finished (manager id=%s, team id=%s)", manager.id, team.id)
