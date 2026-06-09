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
