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
