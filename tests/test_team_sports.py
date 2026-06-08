"""Multi-sport teams: TeamSport through model + default_sport (FK now dropped).

A team's sports live in the TeamSport through model; exactly one row is flagged
is_default. `Team.default_sport` / `Team.sport` (back-compat property) return it.
TeamFactory creates one default TeamSport per team.
"""

import pytest
from django.db import IntegrityError, transaction

from team.models import TeamSport
from tests.factories import SportFactory, TeamFactory

pytestmark = pytest.mark.django_db


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
