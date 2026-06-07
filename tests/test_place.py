"""Managed team Place (Lieu) — CRUD, scoping, and location sync.

Coverage:
  - create place as manager (201); as athlete/non-manager (403); duplicate
    (team, name) (400 place_already_exists).
  - list scoped to member teams; non-member sees none / outside place 404.
  - PATCH / DELETE manager-only; deleting a place nulls Event.place but keeps
    Event.location.
  - setting Event.place_id syncs Event.location and validates same-team.
  - Team.default_place_id syncs default_pool; cross-team default rejected.
  - the data migration logic backfills places from existing locations.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from event.models import Event
from member.models import Member
from place.models import Place
from program.models import Program
from team.models import Team, TeamMembership
from tests.factories import EventFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


def _places_url():
    return reverse("place-list")


def _place_detail_url(place):
    return reverse("place-detail", kwargs={"pk": place.pk})


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
    member = Member.objects.create(
        firstname="A", lastname="T", email=user.email, user=user
    )
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


def test_create_place_as_manager_201(manager_client, team):
    resp = manager_client.post(
        _places_url(),
        {"team": team.pk, "name": "Piscine A", "address": "1 rue X"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["name"] == "Piscine A"
    assert body["team"] == team.pk
    assert Place.objects.filter(team=team, name="Piscine A").exists()


def test_create_place_as_athlete_403(athlete_client, team):
    resp = athlete_client.post(
        _places_url(),
        {"team": team.pk, "name": "Piscine A"},
        format="json",
    )
    assert resp.status_code == 403
    assert not Place.objects.filter(name="Piscine A").exists()


def test_create_place_as_outsider_403(api_client, team):
    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.post(
        _places_url(),
        {"team": team.pk, "name": "Piscine A"},
        format="json",
    )
    assert resp.status_code == 403


def test_create_duplicate_team_name_400(manager_client, team):
    Place.objects.create(team=team, name="Piscine A")
    resp = manager_client.post(
        _places_url(),
        {"team": team.pk, "name": "Piscine A"},
        format="json",
    )
    assert resp.status_code == 400
    assert "place_already_exists" in str(resp.content)


def test_same_name_different_team_ok(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    Place.objects.create(team=team, name="Piscine A")
    resp = manager_client.post(
        _places_url(),
        {"team": other.pk, "name": "Piscine A"},
        format="json",
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# List scoping
# ---------------------------------------------------------------------------


def test_list_scoped_to_member_teams(manager_client, team):
    Place.objects.create(team=team, name="P1")
    Place.objects.create(team=team, name="P2")
    # A place on a team the requester is NOT a member of must not appear.
    other_team = TeamFactory(is_active=True)
    Place.objects.create(team=other_team, name="Hidden")

    resp = manager_client.get(_places_url())
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["results"]} if isinstance(
        resp.json(), dict
    ) else {p["name"] for p in resp.json()}
    assert names == {"P1", "P2"}


def test_list_team_filter(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    Place.objects.create(team=team, name="P1")
    Place.objects.create(team=other, name="P2")

    resp = manager_client.get(_places_url(), {"team": team.pk})
    assert resp.status_code == 200
    data = resp.json()
    rows = data["results"] if isinstance(data, dict) else data
    assert {p["name"] for p in rows} == {"P1"}


def test_athlete_member_can_read(athlete_client, team):
    Place.objects.create(team=team, name="P1")
    resp = athlete_client.get(_places_url())
    assert resp.status_code == 200
    data = resp.json()
    rows = data["results"] if isinstance(data, dict) else data
    assert {p["name"] for p in rows} == {"P1"}


def test_non_member_cannot_retrieve_404(api_client, team):
    place = Place.objects.create(team=team, name="P1")
    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_place_detail_url(place))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH / DELETE manager-only
# ---------------------------------------------------------------------------


def test_patch_manager_only(manager_client, athlete_client, team):
    place = Place.objects.create(team=team, name="P1")

    # athlete cannot patch
    resp = athlete_client.patch(
        _place_detail_url(place), {"name": "P1b"}, format="json"
    )
    assert resp.status_code == 403

    # manager can
    resp = manager_client.patch(
        _place_detail_url(place), {"name": "P1b"}, format="json"
    )
    assert resp.status_code == 200
    place.refresh_from_db()
    assert place.name == "P1b"


def test_delete_manager_only_and_nulls_event_place(manager_client, athlete_client, team):
    place = Place.objects.create(team=team, name="Piscine A")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="Piscine A", place=place)

    # athlete cannot delete
    resp = athlete_client.delete(_place_detail_url(place))
    assert resp.status_code == 403

    # manager can; event.place is nulled, location preserved
    resp = manager_client.delete(_place_detail_url(place))
    assert resp.status_code == 204
    event.refresh_from_db()
    assert event.place is None
    assert event.location == "Piscine A"
    assert not Place.objects.filter(pk=place.pk).exists()


# ---------------------------------------------------------------------------
# Event.place_id sync + same-team validation
# ---------------------------------------------------------------------------


def test_event_place_id_syncs_location(manager_client, team):
    place = Place.objects.create(team=team, name="Piscine A")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="old")

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"place_id": place.pk}, format="json")
    assert resp.status_code == 200, resp.content
    event.refresh_from_db()
    assert event.place_id == place.pk
    assert event.location == "Piscine A"


def test_event_place_id_cross_team_400(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    other_place = Place.objects.create(team=other, name="Foreign")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="x")

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"place_id": other_place.pk}, format="json")
    assert resp.status_code == 400
    assert "place_team_mismatch" in str(resp.content)


def test_event_place_id_null_clears_place(manager_client, team):
    place = Place.objects.create(team=team, name="Piscine A")
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="Piscine A", place=place)

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"place_id": None}, format="json")
    assert resp.status_code == 200, resp.content
    event.refresh_from_db()
    assert event.place is None
    # location free text is left as-is
    assert event.location == "Piscine A"


def test_event_free_text_location_without_place(manager_client, team):
    """Setting only location (no place_id) leaves place untouched/None."""
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="")

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"location": "Stade municipal"}, format="json")
    assert resp.status_code == 200, resp.content
    event.refresh_from_db()
    assert event.location == "Stade municipal"
    assert event.place is None


# ---------------------------------------------------------------------------
# Team.default_place sync
# ---------------------------------------------------------------------------


def test_team_default_place_syncs_default_pool(manager_client, team):
    place = Place.objects.create(team=team, name="Piscine A")
    url = reverse("team-detail", kwargs={"pk": team.pk})
    resp = manager_client.patch(url, {"default_place_id": place.pk}, format="json")
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert team.default_place_id == place.pk
    assert team.default_pool == "Piscine A"


def test_team_default_place_cross_team_400(manager_client, team, manager):
    other = TeamFactory(owner=manager, is_active=True)
    foreign = Place.objects.create(team=other, name="Foreign")
    url = reverse("team-detail", kwargs={"pk": team.pk})
    resp = manager_client.patch(url, {"default_place_id": foreign.pk}, format="json")
    assert resp.status_code == 400
    assert "default_place_team_mismatch" in str(resp.content)


# ---------------------------------------------------------------------------
# Data-migration logic (end state on a fresh team + events)
# ---------------------------------------------------------------------------


def test_backfill_logic_creates_places_and_links(db):
    """Mirror the data migration on live models to assert the end state.

    The migration calls ``place.services.backfill_places`` with historical
    models; here we call it with the live ones — same code path.
    """
    from place.services import backfill_places

    team = TeamFactory(is_active=True, default_pool="Piscine A")
    program = Program.objects.create(name="P", team=team, is_active=True)
    e1 = EventFactory(refer_program=program, location="Piscine A")
    e2 = EventFactory(refer_program=program, location="Piscine B")
    e3 = EventFactory(refer_program=program, location="Piscine A")  # dup
    EventFactory(refer_program=program, location="")  # blank skipped

    backfill_places(Team, Event, Place)

    assert set(Place.objects.filter(team=team).values_list("name", flat=True)) == {
        "Piscine A",
        "Piscine B",
    }
    e1.refresh_from_db()
    e2.refresh_from_db()
    e3.refresh_from_db()
    assert e1.place.name == "Piscine A"
    assert e2.place.name == "Piscine B"
    assert e3.place.name == "Piscine A"

    team.refresh_from_db()
    assert team.default_place is not None
    assert team.default_place.name == "Piscine A"
    assert team.default_pool == "Piscine A"  # canonical string untouched
