"""Multi-sport teams: TeamSport through model + default_sport (FK now dropped).

A team's sports live in the TeamSport through model; exactly one row is flagged
is_default. `Team.default_sport` / `Team.sport` (back-compat property) return it.
TeamFactory creates one default TeamSport per team.
"""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from team.models import TeamSport
from tests.factories import SportFactory, TeamFactory

pytestmark = pytest.mark.django_db


def _team_url(team):
    return reverse("team-detail", kwargs={"pk": team.pk})


def test_factory_attaches_a_default_sport():
    sport = SportFactory()
    team = TeamFactory(sport=sport)
    assert team.default_sport == sport
    assert team.sport == sport  # back-compat property alias
    assert team.team_sports.filter(is_default=True).count() == 1


def test_default_sport_is_none_without_any_teamsport():
    # A team built without the factory's auto-sport has no sports.
    team = TeamFactory(sport=SportFactory())
    team.team_sports.all().delete()
    assert team.default_sport is None
    assert team.sport is None


def test_only_one_default_sport_per_team_is_allowed():
    a = SportFactory(slug="a")
    team = TeamFactory(sport=a)  # default = a
    other = SportFactory(slug="b")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TeamSport.objects.create(team=team, sport=other, is_default=True)


def test_a_team_may_hold_several_sports_with_one_default():
    a = SportFactory(slug="a")
    b = SportFactory(slug="b")
    team = TeamFactory(sport=a)  # default = a
    TeamSport.objects.create(team=team, sport=b, is_default=False)
    assert team.sports.count() == 2
    assert team.default_sport == a


# ---------------------------------------------------------------------------
# API: the multi-sport write surface (sport_ids + default_sport_id) and the
# flattened `sports` read used by the frontend team form.
# ---------------------------------------------------------------------------


def test_api_sports_read_exposes_is_default(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    extra = SportFactory(slug="extra")
    TeamSport.objects.create(team=team, sport=extra, is_default=False, order=1)

    body = auth_client_trainer.get(_team_url(team)).json()
    by_id = {s["id"]: s for s in body["sports"]}
    assert by_id[team.default_sport.id]["is_default"] is True
    assert by_id[extra.id]["is_default"] is False
    # `sport` (read) still surfaces the single default for back-compat callers.
    assert body["sport"]["id"] == team.default_sport.id


def test_api_patch_sport_ids_replaces_set_and_default(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    a = team.default_sport
    b = SportFactory(slug="b")
    c = SportFactory(slug="c")

    resp = auth_client_trainer.patch(
        _team_url(team),
        {"sport_ids": [a.pk, b.pk, c.pk], "default_sport_id": b.pk},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert set(team.sports.values_list("id", flat=True)) == {a.pk, b.pk, c.pk}
    assert team.default_sport == b
    assert team.team_sports.filter(is_default=True).count() == 1


def test_api_patch_sport_ids_removes_unselected(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    a = team.default_sport
    b = SportFactory(slug="b")
    TeamSport.objects.create(team=team, sport=b, is_default=False)

    resp = auth_client_trainer.patch(
        _team_url(team), {"sport_ids": [a.pk]}, format="json"
    )
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert set(team.sports.values_list("id", flat=True)) == {a.pk}
    assert team.default_sport == a  # default kept (still in the set)


def test_api_patch_default_sport_id_flips_default(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    a = team.default_sport
    b = SportFactory(slug="b")
    TeamSport.objects.create(team=team, sport=b, is_default=False)

    resp = auth_client_trainer.patch(
        _team_url(team), {"default_sport_id": b.pk}, format="json"
    )
    assert resp.status_code == 200, resp.content
    team.refresh_from_db()
    assert team.default_sport == b
    assert team.team_sports.get(sport=a).is_default is False


def test_api_default_sport_id_not_in_set_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    a = team.default_sport
    foreign = SportFactory(slug="foreign")

    resp = auth_client_trainer.patch(
        _team_url(team),
        {"sport_ids": [a.pk], "default_sport_id": foreign.pk},
        format="json",
    )
    assert resp.status_code == 400
    assert "default_not_in_sports" in str(resp.content)


def test_api_empty_sport_ids_400(auth_client_trainer, trainer_user):
    team = trainer_user.owned_teams.first()
    resp = auth_client_trainer.patch(
        _team_url(team), {"sport_ids": []}, format="json"
    )
    assert resp.status_code == 400
    assert "no_sports" in str(resp.content)


def test_api_create_team_with_sport_ids(auth_client_trainer, trainer_user):
    trainer_user.team_quota = trainer_user.owned_teams.count() + 1
    trainer_user.save(update_fields=["team_quota"])
    a = SportFactory(slug="ca")
    b = SportFactory(slug="cb")

    resp = auth_client_trainer.post(
        "/api/v1/teams/",
        {
            "name": "Multi Team",
            "sport_ids": [a.pk, b.pk],
            "default_sport_id": b.pk,
            "is_active": True,
            "is_public": False,
            "managers": [],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    from team.models import Team

    team = Team.objects.get(name="Multi Team")
    assert set(team.sports.values_list("id", flat=True)) == {a.pk, b.pk}
    assert team.default_sport == b
