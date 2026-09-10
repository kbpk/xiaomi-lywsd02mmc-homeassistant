from __future__ import annotations

import pytest

from custom_components.lywsd02mmc_local.battery import estimate_cr2032_percentage


@pytest.mark.parametrize(
    ("voltage", "expected"),
    [
        (2.0, 0),
        (2.2, 0),
        (2.804, 67),
        (3.1, 100),
        (3.3, 100),
    ],
)
def test_cr2032_percentage_estimate(voltage: float, expected: int) -> None:
    assert estimate_cr2032_percentage(voltage) == expected
