"""Global multilingual Equipment (Matériel) catalog + per-team enablement.

Coverage:
  - catalog is read-only global reference data (POST -> 405); list returns
    active items to any authenticated user.
  - ?team filter returns the team's enabled subset; non-members get nothing.
  - Team.equipment_ids enables a subset (write) and is read back.
  - Event.equipment_item_ids must be within the team's enabled set
    (equipment_not_enabled otherwise) and syncs the free-text equipment string.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from equipment.models import Equipment
from member.models import Member
from program.models import Program
from team.models import TeamMembership
from tests.factories import EventFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


def _equipment_url():
    return reverse("equipment-list")


@pytest.fixture
def manager():
    return UserFactory()


@pytest.fixture
def team(manager):
    return TeamFactory(owner=manager, is_active=True)


@pytest.fixture
def manager_client(manager):
    client = APIClient()
    client.force_authenticate(user=manager)
    return client


@pytest.fixture
def athlete(team):
    user = UserFactory()
    member = Member.objects.create(firstname="A", lastname="T", email=user.email, user=user)
    TeamMembership.objects.create(team=team, member=member)
    return user


def _rows(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# Catalog: read-only + listing
# ---------------------------------------------------------------------------


def test_catalog_is_read_only(manager_client):
    resp = manager_client.post(_equipment_url(), {"name": "X"}, format="json")
    assert resp.status_code == 405


def test_list_returns_active_catalog(manager_client):
    Equipment.objects.create(name="Pull-buoy")
    Equipment.objects.create(name="Plaquettes")
    Equipment.objects.create(name="Old", is_active=False)

    resp = manager_client.get(_equipment_url())
    assert resp.status_code == 200
    names = {e["name"] for e in _rows(resp)}
    assert "Pull-buoy" in names and "Plaquettes" in names
    assert "Old" not in names  # inactive hidden


def test_team_filter_returns_enabled_subset(manager_client, team):
    e1 = Equipment.objects.create(name="Pull-buoy")
    Equipment.objects.create(name="Plaquettes")  # not enabled
    team.equipment.add(e1)

    resp = manager_client.get(_equipment_url(), {"team": team.pk})
    assert resp.status_code == 200
    assert {e["name"] for e in _rows(resp)} == {"Pull-buoy"}


def test_team_filter_non_member_gets_nothing(api_client, team):
    e1 = Equipment.objects.create(name="Pull-buoy")
    team.equipment.add(e1)
    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)

    resp = api_client.get(_equipment_url(), {"team": team.pk})
    assert resp.status_code == 200
    assert _rows(resp) == []


# ---------------------------------------------------------------------------
# Team enablement (equipment_ids)
# ---------------------------------------------------------------------------


def test_team_equipment_ids_set_and_read(manager_client, team):
    e1 = Equipment.objects.create(name="Pull-buoy")
    e2 = Equipment.objects.create(name="Plaquettes")

    url = reverse("team-detail", kwargs={"pk": team.pk})
    resp = manager_client.patch(url, {"equipment_ids": [e1.pk, e2.pk]}, format="json")
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert set(team.equipment.values_list("id", flat=True)) == {e1.pk, e2.pk}
    assert {e["name"] for e in resp.json()["equipment"]} == {"Pull-buoy", "Plaquettes"}


# ---------------------------------------------------------------------------
# Event.equipment_item_ids restricted to the team's enabled set
# ---------------------------------------------------------------------------


def test_event_equipment_item_must_be_enabled(manager_client, team):
    e1 = Equipment.objects.create(name="Pull-buoy")
    e2 = Equipment.objects.create(name="Plaquettes")
    team.equipment.add(e1)  # only e1 enabled
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program)
    url = reverse("event-detail", kwargs={"pk": event.pk})

    # e2 is not enabled -> 400
    resp = manager_client.patch(url, {"equipment_item_ids": [e2.pk]}, format="json")
    assert resp.status_code == 400
    assert "equipment_not_enabled" in str(resp.content)

    # e1 is enabled -> 200, free-text equipment synced
    resp = manager_client.patch(url, {"equipment_item_ids": [e1.pk]}, format="json")
    assert resp.status_code == 200, resp.content
    event.refresh_from_db()
    assert set(event.equipment_items.values_list("id", flat=True)) == {e1.pk}
    assert event.equipment == "Pull-buoy"


def test_sport_filter(manager_client):
    from sport.models import Sport

    natation = Sport.objects.filter(slug="natation").first()
    other = Sport.objects.create(name="Course", slug="course")
    swim = Equipment.objects.create(name="Pull-buoy", sport=natation)
    Equipment.objects.create(name="Chaussures", sport=other)

    resp = manager_client.get(_equipment_url(), {"sport": natation.pk})
    assert resp.status_code == 200
    ids = {e["id"] for e in _rows(resp)}
    assert swim.pk in ids
    # the other-sport item is excluded
    assert all(e["name"] != "Chaussures" for e in _rows(resp))


def test_seed_migration_populated_catalog(db):
    """The seed migration created the global multilingual Natation catalog."""
    pb = Equipment.objects.filter(name_fr="Pull-buoy").first()
    assert pb is not None
    assert pb.sport is not None and pb.sport.slug == "natation"
    assert Equipment.objects.filter(name_en="Paddles", name_fr="Plaquettes").exists()
