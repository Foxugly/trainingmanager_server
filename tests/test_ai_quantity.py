"""coerce_ai_quantity: the AI's distances / repetitions / counts, whatever their format."""

import pytest

from tools.ai import AIServiceError, coerce_ai_quantity


@pytest.mark.parametrize(
    "value",
    [
        1000,
        1000.0,
        "1000",
        " 1000 ",
        "1,000",
        "1.000",
        "1 000",
        "1 000",  # no-break space
        "1 000",  # narrow no-break space (French typography)
        "1'000",  # Swiss
        "1000m",
        "1000 m",
        "1,000 meters",
        "1.000 mètres",
    ],
)
def test_thousand_meters_in_every_format(value):
    assert coerce_ai_quantity(value, field="distance", minimum=0) == 1000


@pytest.mark.parametrize("value", ["25", 25, "100m", "1,000,000", "12.500"])
def test_other_valid_values(value):
    expected = {"25": 25, 25: 25, "100m": 100, "1,000,000": 1_000_000, "12.500": 12_500}
    assert coerce_ai_quantity(value, field="distance", minimum=0) == expected[value]


@pytest.mark.parametrize(
    "value",
    [1.5, "1.5", "1,5", "1.0000", "10,00", -100, "-100", "abc", "", None, True, [1000], "1 0"],
)
def test_invalid_values_raise_a_clean_ai_error(value):
    with pytest.raises(AIServiceError):
        coerce_ai_quantity(value, field="distance", minimum=0)


def test_minimum_is_enforced():
    assert coerce_ai_quantity(0, field="distance", minimum=0) == 0
    with pytest.raises(AIServiceError):
        coerce_ai_quantity(0, field="repetition", minimum=1)
