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
