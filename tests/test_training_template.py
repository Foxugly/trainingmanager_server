"""GET/PUT /api/v1/teams/{id}/training-template/ — the weekly template.

Coverage:
  - PUT as manager sets slots + default_pool + season dates; GET returns them
    with slots ordered by weekday.
  - PUT with hour_end <= hour_start -> 400; weekday 7 -> 400.
  - PUT as non-manager -> 403; GET as non-member -> 404.
  - A second PUT replaces the template (old slots removed).
"""

import pytest
from django.urls import reverse

from member.models import Member
from team.models import TeamMembership, TrainingSlot
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _url(team):
    return reverse("team-training-template", kwargs={"pk": team.pk})


def _payload(**overrides):
    base = {
        "slots": [
            {"weekday": 2, "hour_start": "18:00", "hour_end": "19:30"},
            {"weekday": 0, "hour_start": "18:00", "hour_end": "19:30"},
        ],
        "default_pool": "Piscine communale",
        "season_start": "2026-09-01",
        "season_end": "2027-06-30",
    }
    base.update(overrides)
    return base


def test_put_as_manager_sets_template_then_get_returns_it_ordered(
    auth_client_trainer, trainer_user
):
    team = trainer_user.owned_teams.first()

    resp = auth_client_trainer.put(_url(team), _payload(), format="json")
    assert resp.status_code == 200, resp.content

    team.refresh_from_db()
    assert team.default_pool == "Piscine communale"
    assert team.season_start.isoformat() == "2026-09-01"
    assert team.season_end.isoformat() == "2027-06-30"
    assert team.training_slots.count() == 2

    get = auth_client_trainer.get(_url(team))
    assert get.status_code == 200
    body = get.json()
    # Slots ordered by weekday (Monday=0 first, then Wednesday=2).
    assert [s["weekday"] for s in body["slots"]] == [0, 2]
    assert body["slots"][0]["hour_start"] == "18:00:00"
    assert body["default_pool"] == "Piscine communale"
    assert body["season_start"] == "2026-09-01"
    assert body["season_end"] == "2027-06-30"


def test_put_hour_end_before_start_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    bad = _payload(slots=[{"weekday": 0, "hour_start": "19:30", "hour_end": "18:00"}])
    resp = auth_client_trainer.put(_url(team), bad, format="json")
    assert resp.status_code == 400
    assert not TrainingSlot.objects.filter(team=team).exists()


def test_put_weekday_7_returns_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    bad = _payload(slots=[{"weekday": 7, "hour_start": "18:00", "hour_end": "19:30"}])
    resp = auth_client_trainer.put(_url(team), bad, format="json")
    assert resp.status_code == 400
    assert not TrainingSlot.objects.filter(team=team).exists()


def test_put_as_non_manager_returns_403(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    # An active athlete member of the team — a member, not a manager.
    athlete = UserFactory()
    member = Member.objects.create(
        firstname="A", lastname="T", email=athlete.email, user=athlete
    )
    TeamMembership.objects.create(team=team, member=member)

    api_client.force_authenticate(user=athlete)
    resp = api_client.put(_url(team), _payload(), format="json")
    assert resp.status_code == 403
    assert not TrainingSlot.objects.filter(team=team).exists()


def test_get_as_non_member_returns_404(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    outsider = UserFactory()
    api_client.force_authenticate(user=outsider)
    resp = api_client.get(_url(team))
    assert resp.status_code == 404


def test_get_as_athlete_member_can_read(api_client, trainer_user):
    team = trainer_user.owned_teams.first()
    TrainingSlot.objects.create(
        team=team, weekday=0, hour_start="18:00", hour_end="19:30"
    )
    athlete = UserFactory()
    member = Member.objects.create(
        firstname="A", lastname="T", email=athlete.email, user=athlete
    )
    TeamMembership.objects.create(team=team, member=member)

    api_client.force_authenticate(user=athlete)
    resp = api_client.get(_url(team))
    assert resp.status_code == 200
    assert len(resp.json()["slots"]) == 1


def test_second_put_replaces_template(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    auth_client_trainer.put(_url(team), _payload(), format="json")
    assert TrainingSlot.objects.filter(team=team).count() == 2
    old_ids = set(TrainingSlot.objects.filter(team=team).values_list("id", flat=True))

    new = _payload(
        slots=[{"weekday": 4, "hour_start": "17:00", "hour_end": "18:00"}],
        default_pool="New pool",
        season_start=None,
        season_end=None,
    )
    resp = auth_client_trainer.put(_url(team), new, format="json")
    assert resp.status_code == 200

    remaining = TrainingSlot.objects.filter(team=team)
    assert remaining.count() == 1
    assert remaining.first().weekday == 4
    # No old slot survived.
    assert not (old_ids & set(remaining.values_list("id", flat=True)))

    team.refresh_from_db()
    assert team.default_pool == "New pool"
    assert team.season_start is None
    assert team.season_end is None
