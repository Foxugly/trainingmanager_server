"""GET /api/v1/teams/{id}/training-template/ — the weekly template (read-only).

Writes go through the per-slot TrainingSlot CRUD (see the second section); this
endpoint only aggregates the template (slots + default_pool + season dates) for
the AI plan generator's prefill.

Coverage:
  - GET returns slots ordered by weekday, with the nested place and the team's
    default_pool / season dates.
  - GET as non-member -> 404; GET as an athlete member is allowed.
"""

import datetime

import pytest
from django.urls import reverse

from member.models import Member
from team.models import TeamMembership, TrainingSlot
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _url(team):
    return reverse("team-training-template", kwargs={"pk": team.pk})


def test_get_returns_ordered_slots_place_and_team_fields(auth_client_trainer, trainer_user):
    """GET aggregates ORM-created slots ordered by weekday, the nested place, and
    the team's default_pool / season dates."""
    from place.models import Place

    team = trainer_user.owned_teams.first()
    place = Place.objects.create(sport=team.sport, name="Bassin nordique")
    team.places.add(place)
    team.default_pool = "Piscine communale"
    team.season_start = datetime.date(2026, 9, 1)
    team.season_end = datetime.date(2027, 6, 30)
    team.save()
    # Created out of weekday order to prove the response is sorted.
    TrainingSlot.objects.create(team=team, weekday=2, hour_start="18:00", hour_end="19:30")
    TrainingSlot.objects.create(
        team=team, weekday=0, hour_start="18:00", hour_end="19:30", place=place
    )

    body = auth_client_trainer.get(_url(team)).json()
    assert [s["weekday"] for s in body["slots"]] == [0, 2]
    by_day = {s["weekday"]: s for s in body["slots"]}
    assert by_day[0]["place"]["name"] == "Bassin nordique"
    assert by_day[2]["place"] is None
    assert body["default_pool"] == "Piscine communale"
    assert body["season_start"] == "2026-09-01"
    assert body["season_end"] == "2027-06-30"


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


# ---------------------------------------------------------------------------
# Per-slot CRUD (each créneau saved on its own) — /teams/{id}/training-slots/
# ---------------------------------------------------------------------------


def _slots_url(team):
    return reverse("team-training-slot-list", kwargs={"team_pk": team.pk})


def _slot_detail_url(team, slot):
    return reverse(
        "team-training-slot-detail", kwargs={"team_pk": team.pk, "pk": slot.pk}
    )


def test_slot_crud_create_patch_delete(auth_client_trainer, trainer_user):
    from place.models import Place

    team = trainer_user.owned_teams.first()
    place = Place.objects.create(sport=team.sport, name="Bassin nordique")
    team.places.add(place)

    # create one slot with a place
    resp = auth_client_trainer.post(
        _slots_url(team),
        {"weekday": 0, "hour_start": "18:00", "hour_end": "19:30", "place_id": place.pk},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    slot = TrainingSlot.objects.get(team=team)
    assert slot.place_id == place.pk
    assert resp.json()["place"]["name"] == "Bassin nordique"

    # patch the hours
    resp = auth_client_trainer.patch(
        _slot_detail_url(team, slot), {"hour_end": "20:00"}, format="json"
    )
    assert resp.status_code == 200, resp.content
    slot.refresh_from_db()
    assert slot.hour_end.strftime("%H:%M") == "20:00"

    # delete it
    resp = auth_client_trainer.delete(_slot_detail_url(team, slot))
    assert resp.status_code == 204
    assert TrainingSlot.objects.filter(team=team).count() == 0


def test_slot_create_rejects_place_not_in_team(auth_client_trainer, trainer_user):
    from place.models import Place

    team = trainer_user.owned_teams.first()
    foreign = Place.objects.create(sport=team.sport, name="Foreign")  # not linked
    resp = auth_client_trainer.post(
        _slots_url(team),
        {"weekday": 1, "hour_start": "18:00", "hour_end": "19:30", "place_id": foreign.pk},
        format="json",
    )
    assert resp.status_code == 400
    assert "place_not_in_team" in str(resp.content)


def test_slot_create_as_non_manager_403(api_client, trainer_user):
    from member.models import Member

    team = trainer_user.owned_teams.first()
    user = UserFactory()
    m = Member.objects.create(firstname="A", lastname="T", email=user.email, user=user)
    TeamMembership.objects.create(team=team, member=m)
    api_client.force_authenticate(user=user)
    resp = api_client.post(
        _slots_url(team),
        {"weekday": 1, "hour_start": "18:00", "hour_end": "19:30"},
        format="json",
    )
    assert resp.status_code == 403


def test_slot_create_defaults_sport_to_team_default(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    resp = auth_client_trainer.post(
        _slots_url(team),
        {"weekday": 1, "hour_start": "18:00", "hour_end": "19:30"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    slot = TrainingSlot.objects.get(team=team)
    assert slot.sport == team.default_sport
    assert resp.json()["sport"]["id"] == team.default_sport.id


def test_slot_create_rejects_sport_not_in_team(auth_client_trainer, trainer_user):
    from tests.factories import SportFactory

    team = trainer_user.owned_teams.first()
    foreign = SportFactory(slug="foreign-slot")
    resp = auth_client_trainer.post(
        _slots_url(team),
        {"weekday": 1, "hour_start": "18:00", "hour_end": "19:30", "sport_id": foreign.pk},
        format="json",
    )
    assert resp.status_code == 400
    assert "sport_not_in_team" in str(resp.content)
