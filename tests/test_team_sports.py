"""Phase 1 of multi-sport teams: TeamSport through model + default_sport.

The legacy Team.sport FK still exists and coexists with the new sports M2M;
Team.default_sport returns the is_default TeamSport, falling back to the FK.
"""

import pytest
from django.db import IntegrityError, transaction

from team.models import TeamSport
from tests.factories import SportFactory, TeamFactory

pytestmark = pytest.mark.django_db


def test_default_sport_falls_back_to_legacy_fk_when_no_teamsport():
    sport = SportFactory()
    team = TeamFactory(sport=sport)
    assert team.team_sports.count() == 0
    assert team.default_sport == sport  # FK fallback during the transition


def test_default_sport_returns_the_flagged_teamsport():
    legacy = SportFactory(slug="legacy")
    running = SportFactory(slug="running")
    team = TeamFactory(sport=legacy)
    TeamSport.objects.create(team=team, sport=running, is_default=True)
    # The flagged M2M row wins over the legacy FK.
    assert team.default_sport == running


def test_only_one_default_sport_per_team_is_allowed():
    a = SportFactory(slug="a")
    b = SportFactory(slug="b")
    team = TeamFactory()
    TeamSport.objects.create(team=team, sport=a, is_default=True)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TeamSport.objects.create(team=team, sport=b, is_default=True)


def test_a_team_may_hold_several_non_default_sports():
    a = SportFactory(slug="a")
    b = SportFactory(slug="b")
    team = TeamFactory()
    TeamSport.objects.create(team=team, sport=a, is_default=True)
    TeamSport.objects.create(team=team, sport=b, is_default=False)
    assert team.sports.count() == 2
    assert team.default_sport == a
