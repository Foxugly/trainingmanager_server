"""Catalog reads (Round, Exercise) are scoped to the caller's teams.

Sharing model:
- *library* content (a round tied to no event and no session template, an
  exercise not reachable only through such team-owned rounds) is shared by
  every user whose teams practise the same (sport, language);
- *session* content (a round linked to an event or a template, and the
  exercises only reachable through such rounds) belongs to that event's team
  and is readable only by that team's owner / managers / active members.
"""

import pytest

from event.models import EventTemplate
from tests.factories import (
    EnergySegmentFactory,
    EventFactory,
    ExerciseFactory,
    MemberFactory,
    ModalityFactory,
    ProgramFactory,
    RoundFactory,
    SportFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def sport():
    return SportFactory()


@pytest.fixture
def foreign_team(sport):
    return TeamFactory(sport=sport, language="fr")


@pytest.fixture
def outsider(sport):
    """A trainer whose own team shares sport + language with the foreign team."""
    user = UserFactory()
    TeamFactory(owner=user, sport=sport, language="fr")
    return user


def _exercise(sport, **kw):
    return ExerciseFactory(
        modality=ModalityFactory(sport=sport), energysegment=EnergySegmentFactory(), **kw
    )


def _ids(response):
    assert response.status_code == 200
    return {row["id"] for row in response.json()["results"]}


def test_round_of_another_team_event_is_not_readable(api_client, sport, foreign_team, outsider):
    event = EventFactory(refer_program=ProgramFactory(team=foreign_team))
    rnd = RoundFactory(sport=sport, language="fr", event=event)
    api_client.force_authenticate(user=outsider)

    assert api_client.get(f"/api/v1/rounds/{rnd.pk}/").status_code == 404
    assert rnd.pk not in _ids(api_client.get("/api/v1/rounds/"))


def test_round_of_another_team_template_is_not_readable(api_client, sport, foreign_team, outsider):
    rnd = RoundFactory(sport=sport, language="fr")
    EventTemplate.objects.create(team=foreign_team, name="T").rounds.add(rnd)
    api_client.force_authenticate(user=outsider)

    assert api_client.get(f"/api/v1/rounds/{rnd.pk}/").status_code == 404


def test_library_round_stays_shared_by_sport_and_language(api_client, sport, outsider):
    rnd = RoundFactory(sport=sport, language="fr")
    api_client.force_authenticate(user=outsider)

    assert rnd.pk in _ids(api_client.get("/api/v1/rounds/"))
    assert api_client.get(f"/api/v1/rounds/{rnd.pk}/").status_code == 200


def test_own_team_event_round_visible_to_manager_and_member(api_client, sport, foreign_team):
    event = EventFactory(refer_program=ProgramFactory(team=foreign_team))
    rnd = RoundFactory(sport=sport, language="fr", event=event)

    api_client.force_authenticate(user=foreign_team.owner)
    assert api_client.get(f"/api/v1/rounds/{rnd.pk}/").status_code == 200

    athlete = UserFactory()
    member = MemberFactory(user=athlete)
    foreign_team.memberships.create(member=member)
    api_client.force_authenticate(user=athlete)
    assert api_client.get(f"/api/v1/rounds/{rnd.pk}/").status_code == 200


def test_round_shared_by_own_and_foreign_event_is_visible(
    api_client, sport, foreign_team, outsider
):
    own_team = outsider.owned_teams.first()
    rnd = RoundFactory(sport=sport, language="fr")
    EventFactory(refer_program=ProgramFactory(team=foreign_team)).rounds.add(rnd)
    EventFactory(refer_program=ProgramFactory(team=own_team)).rounds.add(rnd)
    api_client.force_authenticate(user=outsider)

    assert api_client.get(f"/api/v1/rounds/{rnd.pk}/").status_code == 200


def test_exercise_only_in_another_team_session_is_not_readable(
    api_client, sport, foreign_team, outsider
):
    event = EventFactory(refer_program=ProgramFactory(team=foreign_team))
    rnd = RoundFactory(sport=sport, language="fr", event=event)
    ex = _exercise(sport, round=rnd)
    api_client.force_authenticate(user=outsider)

    assert api_client.get(f"/api/v1/exercises/{ex.pk}/").status_code == 404
    assert ex.pk not in _ids(api_client.get("/api/v1/exercises/"))


def test_library_exercise_stays_shared(api_client, sport, foreign_team, outsider):
    loose = _exercise(sport)
    in_library_round = _exercise(sport, round=RoundFactory(sport=sport, language="fr"))
    # Also used by another team's session: still part of the shared library.
    EventFactory(refer_program=ProgramFactory(team=foreign_team)).rounds.add(
        RoundFactory(sport=sport, language="fr", exercises=[in_library_round])
    )
    api_client.force_authenticate(user=outsider)

    ids = _ids(api_client.get("/api/v1/exercises/"))
    assert {loose.pk, in_library_round.pk} <= ids


def test_clone_exercise_from_another_team_session_is_refused(
    api_client, sport, foreign_team, outsider
):
    event = EventFactory(refer_program=ProgramFactory(team=foreign_team))
    ex = _exercise(sport, round=RoundFactory(sport=sport, language="fr", event=event))
    own_round = RoundFactory(sport=sport, language="fr", author=outsider)
    api_client.force_authenticate(user=outsider)

    resp = api_client.post(
        f"/api/v1/rounds/{own_round.pk}/clone-exercise/",
        {"exercise_id": ex.pk},
        format="json",
    )
    assert resp.status_code == 404
