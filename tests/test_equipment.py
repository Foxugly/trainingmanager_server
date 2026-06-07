"""Managed team Equipment (Matériel) — CRUD, scoping, and equipment sync.

Coverage:
  - create equipment as manager (201); as athlete/non-manager (403); duplicate
    (team, name) (400 equipment_already_exists).
  - list scoped to member teams; team filter; athlete can read; outsider 404.
  - PATCH / DELETE manager-only; deleting an item drops it from events' M2M.
  - setting Event.equipment_item_ids syncs the free-text Event.equipment and
    validates same-team.
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


def _equipment_detail_url(item):
    return reverse("equipment-detail", kwargs={"pk": item.pk})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def athlete_client(athlete):
    client = APIClient()
    client.force_authenticate(user=athlete)
    return client


# ---------------------------------------------------------------------------
# CRUD + permissions
# ---------------------------------------------------------------------------


def test_create_equipment_as_manager_201(manager_client, team):
    resp = manager_client.post(
        _equipment_url(), {"team": team.pk, "name": "Pull-buoy"}, format="json"
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["name"] == "Pull-buoy"
    assert body["team"] == team.pk
    assert Equipment.objects.filter(team=team, name="Pull-buoy").exists()


def test_create_equipment_as_athlete_403(athlete_client, team):
    resp = athlete_client.post(
        _equipment_url(), {"team": team.pk, "name": "Pull-buoy"}, format="json"
    )
    assert resp.status_code == 403
    assert not Equipment.objects.filter(name="Pull-buoy").exists()


def test_create_duplicate_team_name_400(manager_client, team):
    Equipment.objects.create(team=team, name="Plaquettes")
    resp = manager_client.post(
        _equipment_url(), {"team": team.pk, "name": "Plaquettes"}, format="json"
    )
    assert resp.status_code == 400
    assert "equipment_already_exists" in str(resp.content)


def test_same_name_different_team_ok(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    Equipment.objects.create(team=team, name="Palmes")
    resp = manager_client.post(
        _equipment_url(), {"team": other.pk, "name": "Palmes"}, format="json"
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# List scoping
# ---------------------------------------------------------------------------


def _rows(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) else data


def test_list_scoped_to_member_teams(manager_client, team):
    Equipment.objects.create(team=team, name="E1")
    Equipment.objects.create(team=team, name="E2")
    other_team = TeamFactory(is_active=True)
    Equipment.objects.create(team=other_team, name="Hidden")

    resp = manager_client.get(_equipment_url())
    assert resp.status_code == 200
    assert {e["name"] for e in _rows(resp)} == {"E1", "E2"}


def test_list_team_filter(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    Equipment.objects.create(team=team, name="E1")
    Equipment.objects.create(team=other, name="E2")

    resp = manager_client.get(_equipment_url(), {"team": team.pk})
    assert resp.status_code == 200
    assert {e["name"] for e in _rows(resp)} == {"E1"}


def test_athlete_member_can_read(athlete_client, team):
    Equipment.objects.create(team=team, name="E1")
    resp = athlete_client.get(_equipment_url())
    assert resp.status_code == 200
    assert {e["name"] for e in _rows(resp)} == {"E1"}


def test_non_member_cannot_retrieve_404(api_client, team):
    item = Equipment.objects.create(team=team, name="E1")
    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_equipment_detail_url(item))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH / DELETE manager-only
# ---------------------------------------------------------------------------


def test_patch_manager_only(manager_client, athlete_client, team):
    item = Equipment.objects.create(team=team, name="E1")

    resp = athlete_client.patch(_equipment_detail_url(item), {"name": "E1b"}, format="json")
    assert resp.status_code == 403

    resp = manager_client.patch(_equipment_detail_url(item), {"name": "E1b"}, format="json")
    assert resp.status_code == 200
    item.refresh_from_db()
    assert item.name == "E1b"


def test_delete_manager_only_and_drops_from_events(manager_client, athlete_client, team):
    item = Equipment.objects.create(team=team, name="Pull-buoy")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program)
    event.equipment_items.add(item)

    resp = athlete_client.delete(_equipment_detail_url(item))
    assert resp.status_code == 403

    resp = manager_client.delete(_equipment_detail_url(item))
    assert resp.status_code == 204
    assert not Equipment.objects.filter(pk=item.pk).exists()
    assert event.equipment_items.count() == 0


# ---------------------------------------------------------------------------
# Event.equipment_item_ids sync + same-team validation
# ---------------------------------------------------------------------------


def test_event_equipment_item_ids_sync_text(manager_client, team):
    e1 = Equipment.objects.create(team=team, name="Pull-buoy")
    e2 = Equipment.objects.create(team=team, name="Plaquettes")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, equipment="old")

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(
        url, {"equipment_item_ids": [e1.pk, e2.pk]}, format="json"
    )
    assert resp.status_code == 200, resp.content
    event.refresh_from_db()
    assert set(event.equipment_items.values_list("id", flat=True)) == {e1.pk, e2.pk}
    assert event.equipment == "Pull-buoy, Plaquettes"


def test_event_equipment_item_cross_team_400(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    foreign = Equipment.objects.create(team=other, name="Foreign")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program)

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"equipment_item_ids": [foreign.pk]}, format="json")
    assert resp.status_code == 400
    assert "equipment_team_mismatch" in str(resp.content)
