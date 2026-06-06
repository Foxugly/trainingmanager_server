"""GET /api/v1/teams/{id}/pools/ — distinct session locations for autocomplete.

Coverage:
  - team member gets the distinct, non-empty, alphabetically-ordered locations.
  - blank/None locations are excluded; duplicates de-duped.
  - a non-member gets 404.
"""

import pytest
from django.urls import reverse

from member.models import Member
from program.models import Program
from team.models import TeamMembership
from tests.factories import EventFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


def _pools_url(team):
    return reverse("team-pools", kwargs={"pk": team.pk})


def test_pools_returns_distinct_sorted_locations(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team, is_active=True)
    EventFactory(refer_program=program, location="Piscine B")
    EventFactory(refer_program=program, location="Piscine A")
    EventFactory(refer_program=program, location="Piscine A")  # dup
    EventFactory(refer_program=program, location="")  # blank excluded

    resp = auth_client_trainer.get(_pools_url(team))
    assert resp.status_code == 200
    assert resp.json() == {"pools": ["Piscine A", "Piscine B"]}


def test_pools_athlete_member_can_read(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team, is_active=True)
    EventFactory(refer_program=program, location="Piscine X")

    athlete = UserFactory()
    member = Member.objects.create(
        firstname="A", lastname="T", email=athlete.email, user=athlete
    )
    TeamMembership.objects.create(team=team, member=member)

    api_client.force_authenticate(user=athlete)
    resp = api_client.get(_pools_url(team))
    assert resp.status_code == 200
    assert resp.json() == {"pools": ["Piscine X"]}


def test_pools_non_member_404(api_client, trainer_user):
    team = trainer_user.owned_teams.first()

    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_pools_url(team))
    assert resp.status_code == 404


def test_pools_public_team_discoverer_still_404(api_client):
    """A public team is discoverable via /teams/ but its content (pools) is
    not readable by a non-member."""
    public_team = TeamFactory(is_active=True, is_public=True)
    program = Program.objects.create(name="P", team=public_team, is_active=True)
    EventFactory(refer_program=program, location="Secret pool")

    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_pools_url(public_team))
    assert resp.status_code == 404


def test_pools_empty_when_no_events(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    resp = auth_client_trainer.get(_pools_url(team))
    assert resp.status_code == 200
    assert resp.json() == {"pools": []}
