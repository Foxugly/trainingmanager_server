"""The Playwright e2e seed command (`create_e2e_data`) must be idempotent and
produce the known dataset the specs rely on. Pytest runs with DEBUG=True so
the production guard passes; we also assert the guard fires under prod-like
settings.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from customuser.management.commands.create_e2e_data import (
    ATHLETE_EMAIL,
    E2E_PASSWORD,
    MANAGER_EMAIL,
    SPORT_SLUG,
    TEAM_NAME,
)
from member.models import Member
from team.models import Team, TeamMembership

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_create_e2e_data_seeds_expected_entities():
    call_command("create_e2e_data")

    manager = User.objects.get(email=MANAGER_EMAIL)
    assert manager.is_active
    assert manager.check_password(E2E_PASSWORD)
    assert manager.email_confirmed is True

    athlete = User.objects.get(email=ATHLETE_EMAIL)
    assert athlete.check_password(E2E_PASSWORD)
    assert athlete.email_confirmed is True

    team = Team.objects.get(name=TEAM_NAME)
    assert team.owner == manager
    assert team.language == "fr"
    assert team.rsvp_enabled is True
    assert team.sport is not None
    assert team.sport.slug == SPORT_SLUG

    member = Member.objects.get(user=athlete)
    active = TeamMembership.objects.filter(
        team=team, member=member, left_at__isnull=True
    )
    assert active.count() == 1


def test_create_e2e_data_is_idempotent():
    call_command("create_e2e_data")
    call_command("create_e2e_data")

    assert User.objects.filter(email=MANAGER_EMAIL).count() == 1
    assert User.objects.filter(email=ATHLETE_EMAIL).count() == 1
    assert Team.objects.filter(name=TEAM_NAME).count() == 1

    athlete = User.objects.get(email=ATHLETE_EMAIL)
    member = Member.objects.get(user=athlete)
    team = Team.objects.get(name=TEAM_NAME)
    # Still exactly one active membership — no duplicate on re-run.
    assert (
        TeamMembership.objects.filter(
            team=team, member=member, left_at__isnull=True
        ).count()
        == 1
    )


def test_create_e2e_data_refuses_production(settings):
    settings.DEBUG = False
    settings.STATE = "PROD"
    with pytest.raises(CommandError):
        call_command("create_e2e_data")
