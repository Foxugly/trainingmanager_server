"""Coverage of /api/v1/performances/.

Athlete performance records (times / distances / reps / ...) scoped to a
(team, member) pair. Athletes log/view their own; coaches log/view any
athlete in their team. CRUD + filter by team/member/label.
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model

from member.models import Member
from performance.models import Performance
from team.models import TeamMembership
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db

User = get_user_model()

URL = "/api/v1/performances/"


def _detail(pk):
    return f"{URL}{pk}/"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(
        email="perf_coach@local.test", password="pass"
    )


@pytest.fixture
def coach_team(coach_user):
    return TeamFactory(owner=coach_user, is_active=True)


@pytest.fixture
def athlete_user(db):
    return User.objects.create_user(
        email="perf_athlete@local.test", password="pass"
    )


@pytest.fixture
def athlete_member(athlete_user, coach_team):
    member = Member.objects.create(
        firstname="Ath", lastname="Lete", email=athlete_user.email, user=athlete_user
    )
    TeamMembership.objects.create(team=coach_team, member=member)
    return member


@pytest.fixture
def other_member(coach_team):
    """A second athlete in the coach's team, with their own linked user."""
    user = User.objects.create_user(
        email="perf_other@local.test", password="pass"
    )
    member = Member.objects.create(
        firstname="Oth", lastname="Er", email=user.email, user=user
    )
    TeamMembership.objects.create(team=coach_team, member=member)
    return member


@pytest.fixture
def coach_client(api_client, coach_user):
    api_client.force_authenticate(user=coach_user)
    return api_client


@pytest.fixture
def athlete_client(api_client, athlete_user):
    api_client.force_authenticate(user=athlete_user)
    return api_client


@pytest.fixture
def stranger_client(api_client, db):
    user = User.objects.create_user(
        email="perf_stranger@local.test", password="pass"
    )
    api_client.force_authenticate(user=user)
    return api_client


def _payload(team, member, **overrides):
    data = {
        "team": team.pk,
        "member": member.pk,
        "label": "100m freestyle",
        "value": "62.500",
        "unit": "s",
        "recorded_on": "2026-01-15",
        "notes": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 1. Athlete creates own performance; created_by + is_lower_better
# ---------------------------------------------------------------------------


def test_athlete_creates_own_performance(athlete_client, coach_team, athlete_member, athlete_user):
    resp = athlete_client.post(
        URL, _payload(coach_team, athlete_member, unit="s"), format="json"
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["is_lower_better"] is True  # seconds -> lower is better
    assert body["created_by"] == athlete_user.pk
    perf = Performance.objects.get(pk=body["id"])
    assert perf.created_by_id == athlete_user.pk
    assert perf.member_id == athlete_member.pk


def test_is_lower_better_false_for_meters(athlete_client, coach_team, athlete_member):
    resp = athlete_client.post(
        URL,
        _payload(coach_team, athlete_member, label="Cooper test", unit="m", value="2800.000"),
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["is_lower_better"] is False


# ---------------------------------------------------------------------------
# 2. Athlete cannot create for another member -> 403
# ---------------------------------------------------------------------------


def test_athlete_cannot_create_for_other_member(athlete_client, coach_team, other_member):
    resp = athlete_client.post(URL, _payload(coach_team, other_member), format="json")
    assert resp.status_code == 403, resp.json()
    assert not Performance.objects.filter(member=other_member).exists()


# ---------------------------------------------------------------------------
# 3. Coach creates for any athlete in their team -> 201
# ---------------------------------------------------------------------------


def test_coach_creates_for_athlete(coach_client, coach_team, athlete_member, coach_user):
    resp = coach_client.post(URL, _payload(coach_team, athlete_member), format="json")
    assert resp.status_code == 201, resp.json()
    perf = Performance.objects.get(pk=resp.json()["id"])
    assert perf.created_by_id == coach_user.pk
    assert perf.member_id == athlete_member.pk


# ---------------------------------------------------------------------------
# 4. Member not in team -> 400 member_not_in_team
# ---------------------------------------------------------------------------


def test_create_member_not_in_team_returns_400(coach_client, coach_team):
    outsider = Member.objects.create(firstname="Out", lastname="Sider")
    resp = coach_client.post(URL, _payload(coach_team, outsider), format="json")
    assert resp.status_code == 400, resp.json()
    assert resp.json()["code"] == "member_not_in_team"
    assert not Performance.objects.filter(member=outsider).exists()


# ---------------------------------------------------------------------------
# 5. List scoping: athlete sees own; coach sees team; stranger sees none
# ---------------------------------------------------------------------------


def _seed(coach_team, athlete_member, other_member):
    p1 = Performance.objects.create(
        team=coach_team, member=athlete_member, label="100m freestyle",
        value="62.5", unit="s", recorded_on=date(2026, 1, 15),
    )
    p2 = Performance.objects.create(
        team=coach_team, member=other_member, label="Cooper test",
        value="2800", unit="m", recorded_on=date(2026, 1, 16),
    )
    return p1, p2


def test_athlete_list_sees_only_own(athlete_client, coach_team, athlete_member, other_member):
    own, _other = _seed(coach_team, athlete_member, other_member)
    resp = athlete_client.get(URL)
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert ids == {own.pk}


def test_coach_list_with_team_sees_all(coach_client, coach_team, athlete_member, other_member):
    p1, p2 = _seed(coach_team, athlete_member, other_member)
    resp = coach_client.get(URL, {"team": coach_team.pk})
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert ids == {p1.pk, p2.pk}


def test_stranger_list_sees_none(stranger_client, coach_team, athlete_member, other_member):
    _seed(coach_team, athlete_member, other_member)
    resp = stranger_client.get(URL)
    assert resp.status_code == 200
    assert resp.json()["results"] == []


# ---------------------------------------------------------------------------
# 6. Filter by ?member= and ?label=
# ---------------------------------------------------------------------------


def test_filter_by_member(coach_client, coach_team, athlete_member, other_member):
    p1, _p2 = _seed(coach_team, athlete_member, other_member)
    resp = coach_client.get(URL, {"member": athlete_member.pk})
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert ids == {p1.pk}


def test_filter_by_label(coach_client, coach_team, athlete_member, other_member):
    _p1, p2 = _seed(coach_team, athlete_member, other_member)
    resp = coach_client.get(URL, {"label": "cooper"})
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert ids == {p2.pk}


# ---------------------------------------------------------------------------
# 7. Edit / delete permissions
# ---------------------------------------------------------------------------


def test_athlete_edits_and_deletes_own(athlete_client, coach_team, athlete_member):
    perf = Performance.objects.create(
        team=coach_team, member=athlete_member, label="100m",
        value="62.5", unit="s", recorded_on=date(2026, 1, 15),
    )
    resp = athlete_client.patch(_detail(perf.pk), {"value": "60.000"}, format="json")
    assert resp.status_code == 200, resp.json()
    perf.refresh_from_db()
    assert str(perf.value) == "60.000"

    resp = athlete_client.delete(_detail(perf.pk))
    assert resp.status_code == 204
    assert not Performance.objects.filter(pk=perf.pk).exists()


def test_athlete_cannot_edit_others(athlete_client, coach_team, other_member):
    perf = Performance.objects.create(
        team=coach_team, member=other_member, label="Cooper test",
        value="2800", unit="m", recorded_on=date(2026, 1, 16),
    )
    resp = athlete_client.patch(_detail(perf.pk), {"value": "3000.000"}, format="json")
    # Outside the athlete's read scope -> 404 (object not found in queryset).
    assert resp.status_code in (403, 404)
    perf.refresh_from_db()
    assert str(perf.value) == "2800.000"


def test_coach_edits_any_in_team(coach_client, coach_team, athlete_member):
    perf = Performance.objects.create(
        team=coach_team, member=athlete_member, label="100m",
        value="62.5", unit="s", recorded_on=date(2026, 1, 15),
    )
    resp = coach_client.patch(_detail(perf.pk), {"value": "61.000"}, format="json")
    assert resp.status_code == 200, resp.json()
    perf.refresh_from_db()
    assert str(perf.value) == "61.000"


def test_update_ignores_member_and_team_change(coach_client, coach_team, athlete_member, other_member):
    perf = Performance.objects.create(
        team=coach_team, member=athlete_member, label="100m",
        value="62.5", unit="s", recorded_on=date(2026, 1, 15),
    )
    other_team = TeamFactory()
    resp = coach_client.patch(
        _detail(perf.pk),
        {"member": other_member.pk, "team": other_team.pk, "value": "59.000"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    perf.refresh_from_db()
    assert perf.member_id == athlete_member.pk  # unchanged
    assert perf.team_id == coach_team.pk  # unchanged
    assert str(perf.value) == "59.000"


# ---------------------------------------------------------------------------
# 8. Anonymous -> 401/403 on all
# ---------------------------------------------------------------------------


def test_anonymous_blocked(api_client, coach_team, athlete_member):
    perf = Performance.objects.create(
        team=coach_team, member=athlete_member, label="100m",
        value="62.5", unit="s", recorded_on=date(2026, 1, 15),
    )
    assert api_client.get(URL).status_code in (401, 403)
    assert api_client.post(
        URL, _payload(coach_team, athlete_member), format="json"
    ).status_code in (401, 403)
    assert api_client.get(_detail(perf.pk)).status_code in (401, 403)
    assert api_client.patch(
        _detail(perf.pk), {"value": "1.0"}, format="json"
    ).status_code in (401, 403)
    assert api_client.delete(_detail(perf.pk)).status_code in (401, 403)
