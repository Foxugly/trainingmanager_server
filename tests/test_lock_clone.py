import pytest

from tests.factories import (
    EventFactory,
    ExerciseFactory,
    ModalityFactory,
    ProgramFactory,
    RoundFactory,
    TeamFactory,
)

pytestmark = pytest.mark.django_db


# ----------------------------- LOCK ----------------------------------


def test_PATCH_exercise_unlocked_returns_200(auth_client_trainer, trainer_sport):
    modality = ModalityFactory(sport=trainer_sport)
    ex = ExerciseFactory(modality=modality)
    RoundFactory(sport=trainer_sport, exercises=[ex])
    response = auth_client_trainer.patch(
        f"/api/v1/exercises/{ex.pk}/",
        {"notes": "updated"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["notes"] == "updated"


def test_PATCH_exercise_locked_returns_409(auth_client_trainer, trainer_sport):
    modality = ModalityFactory(sport=trainer_sport)
    ex = ExerciseFactory(modality=modality)
    RoundFactory(sport=trainer_sport, exercises=[ex])
    RoundFactory(sport=trainer_sport, exercises=[ex])
    response = auth_client_trainer.patch(
        f"/api/v1/exercises/{ex.pk}/",
        {"notes": "should fail"},
        format="json",
    )
    assert response.status_code == 409


def test_PATCH_shared_exercise_with_round_id_forks_and_relinks(auth_client_trainer, trainer_sport):
    """Editing a shared exercise with round_id forks it: the named round gets a
    clone with the change, the OTHER round keeps the untouched original."""
    modality = ModalityFactory(sport=trainer_sport)
    ex = ExerciseFactory(modality=modality, notes="original")
    r_edit = RoundFactory(sport=trainer_sport, exercises=[ex])
    r_other = RoundFactory(sport=trainer_sport, exercises=[ex])

    response = auth_client_trainer.patch(
        f"/api/v1/exercises/{ex.pk}/",
        {"notes": "forked", "round_id": r_edit.pk},
        format="json",
    )

    assert response.status_code == 200
    new_id = response.json()["id"]
    assert new_id != ex.pk  # a fork, not an in-place edit
    assert response.json()["notes"] == "forked"

    # The edited round now points at the clone; the other round still has the
    # original, which is unchanged.
    assert list(r_edit.exercises.values_list("pk", flat=True)) == [new_id]
    assert list(r_other.exercises.values_list("pk", flat=True)) == [ex.pk]
    ex.refresh_from_db()
    assert ex.notes == "original"


def test_PATCH_shared_exercise_without_round_id_still_409(auth_client_trainer, trainer_sport):
    """No round_id → we can't fork safely, so a shared exercise stays locked."""
    modality = ModalityFactory(sport=trainer_sport)
    ex = ExerciseFactory(modality=modality)
    RoundFactory(sport=trainer_sport, exercises=[ex])
    RoundFactory(sport=trainer_sport, exercises=[ex])
    response = auth_client_trainer.patch(
        f"/api/v1/exercises/{ex.pk}/",
        {"notes": "no round provided"},
        format="json",
    )
    assert response.status_code == 409


def test_PATCH_round_unlocked_returns_200(auth_client_trainer, trainer_user, trainer_sport):
    # Strict ownership: the round's event must belong to a team the caller manages.
    team = trainer_user.owned_teams.first()
    r = RoundFactory(sport=trainer_sport)
    EventFactory(rounds=[r], refer_program=ProgramFactory(team=team))
    response = auth_client_trainer.patch(
        f"/api/v1/rounds/{r.pk}/",
        {"count": 5},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["count"] == 5


def test_PATCH_round_locked_returns_409(auth_client_trainer, trainer_user, trainer_sport):
    # Caller manages the events (passes the ownership gate); the round is shared
    # across two events, so usage_count > 1 yields the 409 lock.
    program = ProgramFactory(team=trainer_user.owned_teams.first())
    r = RoundFactory(sport=trainer_sport)
    EventFactory(rounds=[r], refer_program=program)
    EventFactory(rounds=[r], refer_program=program)
    response = auth_client_trainer.patch(
        f"/api/v1/rounds/{r.pk}/",
        {"count": 9},
        format="json",
    )
    assert response.status_code == 409


# ----------------------------- CLONE ---------------------------------


def test_POST_exercise_clone_returns_201_with_new_id(auth_client_trainer, trainer_sport):
    modality = ModalityFactory(sport=trainer_sport)
    ex = ExerciseFactory(notes="original", modality=modality)
    response = auth_client_trainer.post(
        f"/api/v1/exercises/{ex.pk}/clone/",
        {},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] != ex.pk
    assert body["notes"] == "original"


def test_POST_round_clone_returns_201_with_m2m_exercises_copied(auth_client_trainer, trainer_sport):
    modality = ModalityFactory(sport=trainer_sport)
    r = RoundFactory(sport=trainer_sport, count=3)
    e1 = ExerciseFactory(modality=modality)
    e2 = ExerciseFactory(modality=modality)
    e3 = ExerciseFactory(modality=modality)
    r.exercises.add(e1, e2, e3)

    response = auth_client_trainer.post(
        f"/api/v1/rounds/{r.pk}/clone/",
        {},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] != r.pk
    assert body["count"] == 3
    assert set(body["exercises"]) == {e1.pk, e2.pk, e3.pk}


def test_clone_event_linked_round_of_unmanaged_team_is_forbidden(auth_client_trainer, trainer_sport):
    """IDOR: a trainer cannot clone a round attached to another team's event,
    even when sport/language put it in their read scope."""
    r = RoundFactory(sport=trainer_sport)  # in the caller's (sport, fr) read scope
    other_team = TeamFactory()  # a team the caller does not manage
    program = ProgramFactory(team=other_team)
    event = EventFactory(refer_program=program)
    event.rounds.add(r)

    response = auth_client_trainer.post(f"/api/v1/rounds/{r.pk}/clone/", {}, format="json")
    assert response.status_code == 403, response.json()


def test_POST_round_clone_exercise_attaches_to_round(auth_client_trainer, trainer_sport):
    modality = ModalityFactory(sport=trainer_sport)
    r = RoundFactory(sport=trainer_sport)
    ex = ExerciseFactory(notes="source", modality=modality)
    response = auth_client_trainer.post(
        f"/api/v1/rounds/{r.pk}/clone-exercise/",
        {"exercise_id": ex.pk},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    new_id = body["id"]
    assert new_id != ex.pk
    assert body["notes"] == "source"
    r.refresh_from_db()
    assert r.exercises.filter(pk=new_id).exists()


def test_POST_round_clone_exercise_missing_id_returns_400(auth_client_trainer, trainer_sport):
    r = RoundFactory(sport=trainer_sport)
    response = auth_client_trainer.post(
        f"/api/v1/rounds/{r.pk}/clone-exercise/",
        {},
        format="json",
    )
    assert response.status_code == 400


def test_POST_round_clone_exercise_unknown_id_returns_404(auth_client_trainer, trainer_sport):
    r = RoundFactory(sport=trainer_sport)
    response = auth_client_trainer.post(
        f"/api/v1/rounds/{r.pk}/clone-exercise/",
        {"exercise_id": 999999},
        format="json",
    )
    assert response.status_code == 404


# ------------------------- PERMISSION 403 ----------------------------


def test_PATCH_exercise_as_non_trainer_returns_403(auth_client_non_trainer):
    ex = ExerciseFactory()
    response = auth_client_non_trainer.patch(
        f"/api/v1/exercises/{ex.pk}/",
        {"notes": "forbidden"},
        format="json",
    )
    assert response.status_code == 403


def test_clone_exercise_as_non_trainer_returns_403(auth_client_non_trainer):
    ex = ExerciseFactory()
    response = auth_client_non_trainer.post(
        f"/api/v1/exercises/{ex.pk}/clone/",
        {},
        format="json",
    )
    assert response.status_code == 403


def test_clone_round_as_non_trainer_returns_403(auth_client_non_trainer):
    r = RoundFactory()
    response = auth_client_non_trainer.post(
        f"/api/v1/rounds/{r.pk}/clone/",
        {},
        format="json",
    )
    assert response.status_code == 403


def test_PATCH_round_of_unmanaged_team_event_returns_403(
    auth_client_trainer, trainer_sport
):
    """IDOR guard: a trainer in the sport scope cannot mutate a round whose only
    linked event belongs to a team they do not manage."""
    foreign_program = ProgramFactory(team=TeamFactory(sport=trainer_sport))
    r = RoundFactory(sport=trainer_sport)
    EventFactory(rounds=[r], refer_program=foreign_program)
    response = auth_client_trainer.patch(
        f"/api/v1/rounds/{r.pk}/",
        {"count": 7},
        format="json",
    )
    assert response.status_code == 403
