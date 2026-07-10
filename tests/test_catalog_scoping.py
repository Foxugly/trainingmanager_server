"""Catalog (Exercise, Round) is scoped by the user's (sport, language)
pairs of accessible teams.

A user only sees exercises/rounds whose sport AND language match at
least one of their active teams' (sport, language) tuples.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.factories import (
    EnergySegmentFactory,
    ExerciseFactory,
    ModalityFactory,
    RoundFactory,
    SportFactory,
    TeamFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def test_GET_exercises_only_returns_user_sport_exercises(api_client):
    natation = SportFactory(slug="natation-scoping", name="Natation Scoping")
    course = SportFactory(slug="course-scoping", name="Course Scoping")

    user = UserFactory()
    TeamFactory(owner=user, sport=natation, is_active=True)

    nat_modality = ModalityFactory(sport=natation)
    crs_modality = ModalityFactory(sport=course)
    seg = EnergySegmentFactory()

    nat_exercise = ExerciseFactory(modality=nat_modality, energysegment=seg)
    crs_exercise = ExerciseFactory(modality=crs_modality, energysegment=seg)

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/exercises/")
    assert response.status_code == 200
    ids = {e["id"] for e in response.json()["results"]}
    assert nat_exercise.pk in ids
    assert crs_exercise.pk not in ids


def test_GET_rounds_only_returns_user_sport_rounds(api_client):
    natation = SportFactory(slug="natation-scoping-r", name="Natation R")
    course = SportFactory(slug="course-scoping-r", name="Course R")

    user = UserFactory()
    TeamFactory(owner=user, sport=natation, is_active=True)

    nat_round = RoundFactory(sport=natation)
    crs_round = RoundFactory(sport=course)

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/rounds/")
    assert response.status_code == 200
    ids = {r["id"] for r in response.json()["results"]}
    assert nat_round.pk in ids
    assert crs_round.pk not in ids


def test_GET_exercises_user_without_team_sees_nothing(api_client):
    """A user with no team membership must not see any exercise."""
    sport = SportFactory(slug="lonely-natation", name="Lonely")
    modality = ModalityFactory(sport=sport)
    seg = EnergySegmentFactory()
    ExerciseFactory(modality=modality, energysegment=seg)

    user = UserFactory()
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/exercises/")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_GET_rounds_user_without_team_sees_nothing(api_client):
    sport = SportFactory(slug="lonely-natation-r", name="Lonely R")
    RoundFactory(sport=sport)

    user = UserFactory()
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/rounds/")
    assert response.status_code == 200
    assert response.json()["count"] == 0


# ----------------------- (sport, language) scoping --------------------


def test_GET_exercises_filtered_by_sport_AND_language(api_client):
    natation = SportFactory(slug="nat-lang", name="Nat Lang")
    user = UserFactory(language="fr")
    TeamFactory(owner=user, sport=natation, language="fr", is_active=True)

    modality = ModalityFactory(sport=natation)
    seg = EnergySegmentFactory()
    fr_exercise = ExerciseFactory(modality=modality, energysegment=seg, language="fr")
    it_exercise = ExerciseFactory(modality=modality, energysegment=seg, language="it")
    es_exercise = ExerciseFactory(modality=modality, energysegment=seg, language="es")

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/exercises/")
    assert response.status_code == 200
    ids = {e["id"] for e in response.json()["results"]}
    assert fr_exercise.pk in ids
    assert it_exercise.pk not in ids
    assert es_exercise.pk not in ids


def test_GET_rounds_filtered_by_sport_AND_language(api_client):
    natation = SportFactory(slug="nat-lang-r", name="Nat Lang R")
    user = UserFactory(language="fr")
    TeamFactory(owner=user, sport=natation, language="fr", is_active=True)

    fr_round = RoundFactory(sport=natation, language="fr")
    it_round = RoundFactory(sport=natation, language="it")
    es_round = RoundFactory(sport=natation, language="es")

    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/rounds/")
    assert response.status_code == 200
    ids = {r["id"] for r in response.json()["results"]}
    assert fr_round.pk in ids
    assert it_round.pk not in ids
    assert es_round.pk not in ids


# ----------------------- consistency validation -----------------------


def test_PATCH_round_with_mismatching_language_returns_400(
    api_client, auth_client_trainer, trainer_sport
):
    fr_round = RoundFactory(sport=trainer_sport, language="fr")
    es_modality = ModalityFactory(sport=trainer_sport)
    es_exercise = ExerciseFactory(
        modality=es_modality, energysegment=EnergySegmentFactory(), language="es"
    )

    response = auth_client_trainer.patch(
        f"/api/v1/rounds/{fr_round.pk}/",
        {"exercises": [es_exercise.pk]},
        format="json",
    )
    assert response.status_code == 400


# --------------------- clone-exercise scoping (HIGH) ------------------


def test_clone_exercise_cross_language_returns_404(auth_client_trainer, trainer_sport):
    """Cloning an exercise from a different language must return 404 (back-door read prevention)."""
    fr_round = RoundFactory(sport=trainer_sport, language="fr")
    modality = ModalityFactory(sport=trainer_sport)
    it_exercise = ExerciseFactory(
        modality=modality, energysegment=EnergySegmentFactory(), language="it"
    )

    response = auth_client_trainer.post(
        f"/api/v1/rounds/{fr_round.pk}/clone-exercise/",
        {"exercise_id": it_exercise.pk},
        format="json",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "exercise_not_found"


def test_clone_exercise_cross_sport_returns_404(auth_client_trainer, trainer_sport):
    """Cloning an exercise from a different sport must return 404."""
    fr_round = RoundFactory(sport=trainer_sport, language="fr")
    other_sport = SportFactory(slug="other-sport-clone", name="Other Sport Clone")
    other_modality = ModalityFactory(sport=other_sport)
    other_exercise = ExerciseFactory(
        modality=other_modality, energysegment=EnergySegmentFactory(), language="fr"
    )

    response = auth_client_trainer.post(
        f"/api/v1/rounds/{fr_round.pk}/clone-exercise/",
        {"exercise_id": other_exercise.pk},
        format="json",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "exercise_not_found"


# --------------------- ?language= filter -----------------------------


def test_GET_exercises_filter_by_language(auth_client_trainer, trainer_user, trainer_sport):
    """A multi-team trainer can narrow the catalog by ?language=."""
    # Add a second team in 'nl' so the trainer's accessible pairs cover both fr and nl
    from tests.factories import TeamFactory

    TeamFactory(owner=trainer_user, sport=trainer_sport, language="nl", is_active=True)

    modality = ModalityFactory(sport=trainer_sport)
    seg = EnergySegmentFactory()
    fr_ex = ExerciseFactory(modality=modality, energysegment=seg, language="fr")
    nl_ex = ExerciseFactory(modality=modality, energysegment=seg, language="nl")

    response = auth_client_trainer.get("/api/v1/exercises/?language=fr")
    assert response.status_code == 200
    ids = {e["id"] for e in response.json()["results"]}
    assert fr_ex.pk in ids
    assert nl_ex.pk not in ids
    for e in response.json()["results"]:
        assert e["language"] == "fr"


# ----------------------------- inheritance ----------------------------


def test_clone_round_inherits_language(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    team.language = "nl"
    team.save()
    nl_round = RoundFactory(sport=trainer_sport, language="nl")
    response = auth_client_trainer.post(f"/api/v1/rounds/{nl_round.pk}/clone/", {}, format="json")
    assert response.status_code == 201
    assert response.json()["language"] == "nl"


def test_clone_exercise_inherits_language(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    team.language = "nl"
    team.save()
    modality = ModalityFactory(sport=trainer_sport)
    nl_exercise = ExerciseFactory(
        modality=modality, energysegment=EnergySegmentFactory(), language="nl"
    )
    response = auth_client_trainer.post(
        f"/api/v1/exercises/{nl_exercise.pk}/clone/", {}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["language"] == "nl"


def test_generate_training_uses_team_language(auth_client_trainer, trainer_user, settings):
    """Generated rounds and exercises adopt the team's language."""
    from event.models import Event
    from program.models import Program
    from round.models import Round

    settings.ANTHROPIC_API_KEY = "sk-ant-fake"
    team = trainer_user.owned_teams.first()
    team.language = "nl"
    team.save()
    sport = team.sport

    program = Program.objects.create(name="NL prog", team=team)
    event = Event.objects.create(
        name="NL event", refer_program=program, total=1000, total_target=1000
    )

    modality = ModalityFactory(sport=sport)
    segment = EnergySegmentFactory()

    # Build a fake Anthropic tool_use response with one round + one exercise
    response_obj = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "create_training_session"
    tool_block.input = {
        "rounds": [
            {
                "count": 1,
                "t_start": "00:00",
                "t_break": "00:00",
                "exercises": [
                    {
                        "modality_id": modality.pk,
                        "energysegment_id": segment.pk,
                        "distance": 100,
                        "repetition": 4,
                        "t_start": "00:00",
                        "t_break": "00:00",
                        "notes": "warm-up",
                    }
                ],
            }
        ],
        "rationale": "Plan",
    }
    response_obj.content = [tool_block]
    response_obj.model = "claude-haiku-4-5-20251001"
    response_obj.usage.input_tokens = 10
    response_obj.usage.output_tokens = 5
    response_obj.stop_reason = "tool_use"

    with patch("tools.ai.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.create.return_value = response_obj
        api_response = auth_client_trainer.post(
            f"/api/v1/events/{event.pk}/generate-training/", {}, format="json"
        )
    assert api_response.status_code == 200, api_response.json()
    created_round = Round.objects.filter(sport=sport, language="nl").first()
    assert created_round is not None
    assert all(ex.language == "nl" for ex in created_round.exercises.all())
