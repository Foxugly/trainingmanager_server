"""Venues (Lieux) — global sport-scoped pool, shared with teams via M2M.

Coverage:
  - create a place links it to a team (manager) and stamps the team's sport;
    athlete/outsider cannot.
  - list ?team -> the team's linked places (members only); ?sport -> the pool.
  - a place can belong to several teams in parallel (M2M).
  - delete by a manager of a linked team removes it everywhere; Event.place is
    nulled, Event.location kept.
  - Event.place_id must be one of the team's places (place_not_in_team) and
    syncs Event.location.
  - Team.place_ids attaches existing places; Team.default_place_id syncs
    default_pool and is auto-added to the team's places.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from member.models import Member
from place.models import Place
from program.models import Program
from team.models import TeamMembership
from tests.factories import EventFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


def _make_place(name, team):
    """Create a venue tied to the team's sports (Place is multi-sport now)."""
    from place.models import Place

    p = Place.objects.create(name=name)
    p.sports.set(team.sports.all())
    return p


def _places_url():
    return reverse("place-list")


def _place_detail_url(place):
    return reverse("place-detail", kwargs={"pk": place.pk})


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


def _rows(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# Create links to a team + stamps sport
# ---------------------------------------------------------------------------


def test_create_place_links_team_and_sets_sport(manager_client, team):
    resp = manager_client.post(
        _places_url(),
        {"team": team.pk, "name": "Piscine A", "address": "1 rue X"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    place = Place.objects.get(name="Piscine A")
    # Multi-sport: the venue serves the creating team's sports.
    assert place.sports.filter(pk=team.default_sport.id).exists()
    assert team.places.filter(pk=place.pk).exists()


def test_create_place_as_athlete_403(athlete_client, team):
    resp = athlete_client.post(
        _places_url(), {"team": team.pk, "name": "Piscine A"}, format="json"
    )
    assert resp.status_code == 403
    assert not Place.objects.filter(name="Piscine A").exists()


# ---------------------------------------------------------------------------
# Listing: ?team (linked) and ?sport (pool)
# ---------------------------------------------------------------------------


def test_list_team_filter_returns_linked(manager_client, team):
    linked = _make_place("P1", team)
    team.places.add(linked)
    _make_place("P2", team)  # not linked

    resp = manager_client.get(_places_url(), {"team": team.pk})
    assert resp.status_code == 200
    assert {p["name"] for p in _rows(resp)} == {"P1"}


def test_list_sport_filter_returns_pool(manager_client, team):
    p1 = _make_place("P1", team)
    _make_place("P2", team)  # not linked to my team but shares the sport (pool)
    team.places.add(p1)

    resp = manager_client.get(_places_url(), {"sport": team.sport_id})
    assert resp.status_code == 200
    names = {p["name"] for p in _rows(resp)}
    assert {"P1", "P2"} <= names


def test_non_member_cannot_retrieve_404(api_client, team):
    place = _make_place("P1", team)
    team.places.add(place)
    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_place_detail_url(place))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Shared across teams (M2M, parallel)
# ---------------------------------------------------------------------------


def test_place_shared_across_teams(manager_client, manager, team):
    other = TeamFactory(owner=manager, is_active=True, sport=team.sport)
    place = _make_place("Shared pool", team)
    team.places.add(place)
    other.places.add(place)

    assert place.teams.count() == 2
    # visible from both teams' filters
    assert {p["name"] for p in _rows(manager_client.get(_places_url(), {"team": team.pk}))} == {
        "Shared pool"
    }
    assert {p["name"] for p in _rows(manager_client.get(_places_url(), {"team": other.pk}))} == {
        "Shared pool"
    }


# ---------------------------------------------------------------------------
# Delete (manager of a linked team) — global, nulls Event.place
# ---------------------------------------------------------------------------


def test_delete_by_linked_manager_nulls_event_place(manager_client, athlete_client, team):
    place = _make_place("Piscine A", team)
    team.places.add(place)
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="Piscine A", place=place)

    resp = athlete_client.delete(_place_detail_url(place))
    assert resp.status_code in (403, 404)

    resp = manager_client.delete(_place_detail_url(place))
    assert resp.status_code == 204
    event.refresh_from_db()
    assert event.place is None
    assert event.location == "Piscine A"
    assert not Place.objects.filter(pk=place.pk).exists()


# ---------------------------------------------------------------------------
# Event.place_id must be in the team's places + syncs location
# ---------------------------------------------------------------------------


def test_event_place_id_syncs_location(manager_client, team):
    place = _make_place("Piscine A", team)
    team.places.add(place)
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="old")

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"place_id": place.pk}, format="json")
    assert resp.status_code == 200, resp.content
    event.refresh_from_db()
    assert event.place_id == place.pk
    assert event.location == "Piscine A"


def test_event_place_not_in_team_400(manager_client, team):
    foreign = _make_place("Foreign", team)  # not linked
    program = Program.objects.create(name="P", team=team, is_active=True)
    event = EventFactory(refer_program=program, location="x")

    url = reverse("event-detail", kwargs={"pk": event.pk})
    resp = manager_client.patch(url, {"place_id": foreign.pk}, format="json")
    assert resp.status_code == 400
    assert "place_not_in_team" in str(resp.content)


# ---------------------------------------------------------------------------
# Team.place_ids + default_place_id
# ---------------------------------------------------------------------------


def test_team_place_ids_attaches_places(manager_client, team):
    p1 = _make_place("P1", team)
    p2 = _make_place("P2", team)
    url = reverse("team-detail", kwargs={"pk": team.pk})
    resp = manager_client.patch(url, {"place_ids": [p1.pk, p2.pk]}, format="json")
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert set(team.places.values_list("id", flat=True)) == {p1.pk, p2.pk}


def test_team_place_ids_rejects_foreign_sport_place(manager_client, team):
    """A venue serving only an unrelated sport cannot be linked to the team."""
    from sport.models import Sport

    from place.models import Place

    other_sport = Sport.objects.create(name="Course", slug="course-foreign")
    foreign = Place.objects.create(name="Piste")
    foreign.sports.set([other_sport])  # not one of the team's sports

    url = reverse("team-detail", kwargs={"pk": team.pk})
    resp = manager_client.patch(url, {"place_ids": [foreign.pk]}, format="json")
    assert resp.status_code == 400
    assert "place_not_in_sport" in str(resp.content)
    team.refresh_from_db()
    assert not team.places.filter(pk=foreign.pk).exists()


def test_team_default_place_syncs_pool_and_autolinks(manager_client, team):
    place = _make_place("Piscine A", team)
    url = reverse("team-detail", kwargs={"pk": team.pk})
    resp = manager_client.patch(url, {"default_place_id": place.pk}, format="json")
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert team.default_place_id == place.pk
    assert team.default_pool == "Piscine A"
    # default is auto-added to the team's places
    assert team.places.filter(pk=place.pk).exists()
