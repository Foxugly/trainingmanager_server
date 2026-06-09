import pytest
from tools.choices import TrainingType

pytestmark = pytest.mark.django_db


def test_training_type_values():
    assert TrainingType.STRUCTURED == "structured"
    assert TrainingType.FREEFORM == "freeform"
    assert set(TrainingType.values) == {"structured", "freeform"}
