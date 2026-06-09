import pytest
from tools.choices import TrainingType

pytestmark = pytest.mark.django_db


def test_training_type_values():
    assert TrainingType.STRUCTURED == "structured"
    assert TrainingType.FREEFORM == "freeform"
    assert set(TrainingType.values) == {"structured", "freeform"}


from tests.factories import SportFactory


def test_sport_default_training_type_defaults_structured():
    sport = SportFactory()
    assert sport.default_training_type == TrainingType.STRUCTURED


from team.models import TeamSport
from tests.factories import TeamFactory


def test_teamsport_training_type_nullable_default_none():
    team = TeamFactory()
    ts = TeamSport.objects.filter(team=team).first()
    assert ts is not None
    assert ts.training_type is None


from tests.factories import EventFactory


def test_event_training_fields_defaults():
    event = EventFactory()
    assert event.training_type == TrainingType.STRUCTURED
    assert event.training_richtext == ""


from program.models import Program
from team.models import TeamSport
from tests.factories import TeamFactory, EventFactory, SportFactory


def _event_for(team, sport):
    program = Program.objects.create(name="P", team=team)
    return EventFactory(refer_program=program, sport=sport)


def test_cascade_sport_default_only():
    sport = SportFactory()
    team = TeamFactory(sport=sport)
    event = _event_for(team, sport)
    assert event.resolve_default_training_type() == TrainingType.STRUCTURED


def test_cascade_team_override_beats_sport_default():
    sport = SportFactory()
    team = TeamFactory(sport=sport)
    ts = TeamSport.objects.get(team=team, sport=sport)
    ts.training_type = TrainingType.FREEFORM
    ts.save()
    event = _event_for(team, sport)
    assert event.resolve_default_training_type() == TrainingType.FREEFORM


def test_cascade_falls_back_when_no_sport():
    team = TeamFactory()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=None)
    assert event.resolve_default_training_type() == TrainingType.STRUCTURED


def test_event_create_seeds_training_type_from_cascade(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    ts = TeamSport.objects.get(team=team, sport=trainer_sport)
    ts.training_type = TrainingType.FREEFORM
    ts.save()
    program = Program.objects.create(name="P", team=team)
    resp = auth_client_trainer.post(
        "/api/v1/events/",
        {"name": "E", "refer_program_id": program.pk, "sport_id": trainer_sport.pk},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["training_type"] == "freeform"


def test_event_patch_freeform_richtext_is_sanitized(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.FREEFORM)
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/",
        {"training_richtext": "<p>Swim</p><script>alert(1)</script>"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert "<script>" not in event.training_richtext
    assert "<p>Swim</p>" in event.training_richtext


def test_switch_to_freeform_clears_rounds(auth_client_trainer, trainer_user, trainer_sport):
    from round.models import Round
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.STRUCTURED)
    r = Round.objects.create(sport=trainer_sport, language="fr", order=1, count=1)
    event.rounds.add(r)
    resp = auth_client_trainer.patch(f"/api/v1/events/{event.pk}/", {"training_type": "freeform"}, format="json")
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert event.rounds.count() == 0


def test_switch_to_structured_clears_richtext(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.FREEFORM, training_richtext="<p>x</p>")
    resp = auth_client_trainer.patch(f"/api/v1/events/{event.pk}/", {"training_type": "structured"}, format="json")
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert event.training_richtext == ""


def test_sport_admin_can_set_default_training_type(api_client):
    from tests.factories import UserFactory, SportFactory
    staff = UserFactory(is_staff=True)
    api_client.force_authenticate(user=staff)
    sport = SportFactory()
    resp = api_client.patch(
        f"/api/v1/sports/{sport.pk}/", {"default_training_type": "freeform"}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    sport.refresh_from_db()
    assert sport.default_training_type == "freeform"


from team.models import TeamSport


def test_team_sports_read_includes_training_type(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    ts = TeamSport.objects.get(team=team, sport=trainer_sport)
    ts.training_type = TrainingType.FREEFORM
    ts.save()
    resp = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/")
    assert resp.status_code == 200
    sports = resp.json()["sports"]
    row = next(s for s in sports if s["id"] == trainer_sport.pk)
    assert row["training_type"] == "freeform"


def test_team_can_set_per_sport_training_type(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    resp = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"sport_training_types": [{"sport_id": trainer_sport.pk, "training_type": "freeform"}]},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    ts = TeamSport.objects.get(team=team, sport=trainer_sport)
    assert ts.training_type == "freeform"


from unittest.mock import patch


def test_generate_freeform_training_returns_html(trainer_user, trainer_sport):
    from event.ai import generate_freeform_training
    from program.models import Program
    from tests.factories import EventFactory
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.FREEFORM)
    fake = {"tool_input": {"html": "<p>Easy run 30'</p>", "rationale": "Recovery day."},
            "model": "claude-x", "input_tokens": 10, "output_tokens": 5}
    with patch("event.ai.call_claude_with_tool", return_value=fake) as mocked:
        out = generate_freeform_training(event=event, user=trainer_user, additional_prompt="easy")
    assert out["html"] == "<p>Easy run 30'</p>"
    assert out["rationale"] == "Recovery day."
    assert mocked.called
