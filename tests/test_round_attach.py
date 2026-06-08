"""POST /rounds/ with optional event_id (writeOnly) — atomic attach.

The frontend creates a Round and attaches it to an Event in a single request
to avoid the orphan-on-failure risk of a 2-step POST + PATCH M2M chain.

Permission: the request user must manage the team behind the target event.
"""

import pytest

from event.models import Event
from program.models import Program
from tests.factories import EventFactory, ProgramFactory, TeamFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_POST_round_with_event_id_attaches_atomically(
    auth_client_trainer, trainer_user, trainer_sport
):
    """Manager of the event's team: round is created AND attached in one shot."""
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    event = EventFactory(refer_program=program)

    response = auth_client_trainer.post(
        "/api/v1/rounds/",
        {
            "sport_id": trainer_sport.pk,
            "language": team.language,
            "order": 1,
            "count": 1,
            "event_id": event.pk,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    new_round_id = body["id"]
    # event_id is writeOnly: the response does not echo it
    assert "event_id" not in body
    event.refresh_from_db()
    assert event.rounds.filter(pk=new_round_id).exists()


def test_POST_round_without_event_id_creates_library_round(auth_client_trainer, trainer_sport):
    """Backward-compatible path: no event_id → library round, no attach."""
    response = auth_client_trainer.post(
        "/api/v1/rounds/",
        {
            "sport_id": trainer_sport.pk,
            "language": "fr",
            "order": 1,
            "count": 1,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    new_round_id = response.json()["id"]
    # Library round = not attached to any event
    assert not Event.objects.filter(rounds__pk=new_round_id).exists()


def test_POST_round_with_event_id_non_manager_returns_403(auth_client_trainer, trainer_sport):
    """Trainer (manages own team) but not manager of the event's team → 403."""
    other_owner = UserFactory()
    other_team = TeamFactory(owner=other_owner, sport=trainer_sport, is_active=True)
    other_program = ProgramFactory(team=other_team)
    other_event = EventFactory(refer_program=other_program)

    response = auth_client_trainer.post(
        "/api/v1/rounds/",
        {
            "sport_id": trainer_sport.pk,
            "language": "fr",
            "order": 1,
            "count": 1,
            "event_id": other_event.pk,
        },
        format="json",
    )
    assert response.status_code == 403, response.json()
    assert response.json()["code"] == "not_authorized_event"
    # The round must NOT have been created (atomicity from the user's perspective:
    # a 403 means no orphan round was left behind from a hijacked attempt).
    other_event.refresh_from_db()
    assert other_event.rounds.count() == 0


def test_library_round_mutation_restricted_to_author(api_client, trainer_sport):
    """A library round (no events) carries its author; another same-(sport,
    language) trainer cannot edit/delete it, only the author (or staff)."""
    from round.models import Round

    author = UserFactory()
    TeamFactory(owner=author, sport=trainer_sport, is_active=True)  # makes them a trainer
    api_client.force_authenticate(user=author)
    created = api_client.post(
        "/api/v1/rounds/",
        {"sport_id": trainer_sport.pk, "language": "fr", "order": 1, "count": 1},
        format="json",
    )
    assert created.status_code == 201, created.json()
    rid = created.json()["id"]
    assert Round.objects.get(pk=rid).author_id == author.pk

    # Another trainer with a same-(sport, language) team can see it but not mutate.
    other = UserFactory()
    TeamFactory(owner=other, sport=trainer_sport, is_active=True)
    api_client.force_authenticate(user=other)
    assert api_client.patch(f"/api/v1/rounds/{rid}/", {"count": 5}, format="json").status_code == 403
    assert api_client.delete(f"/api/v1/rounds/{rid}/").status_code == 403

    # The author may edit it.
    api_client.force_authenticate(user=author)
    ok = api_client.patch(f"/api/v1/rounds/{rid}/", {"count": 5}, format="json")
    assert ok.status_code == 200, ok.content
    assert Round.objects.get(pk=rid).count == 5


def test_POST_round_with_event_id_as_team_manager_succeeds(api_client, trainer_sport):
    """Manager (not owner) of the event's active team can attach. Confirms the
    permission check uses managed_teams (owner OR manager), not owner-only."""
    owner = UserFactory()
    manager = UserFactory()
    team = TeamFactory(owner=owner, sport=trainer_sport, is_active=True)
    team.managers.add(manager)
    program = Program.objects.create(name="P", team=team)
    event = Event.objects.create(name="E", refer_program=program)

    api_client.force_authenticate(user=manager)
    response = api_client.post(
        "/api/v1/rounds/",
        {
            "sport_id": trainer_sport.pk,
            "language": team.language,
            "order": 1,
            "count": 1,
            "event_id": event.pk,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    new_round_id = response.json()["id"]
    event.refresh_from_db()
    assert event.rounds.filter(pk=new_round_id).exists()
