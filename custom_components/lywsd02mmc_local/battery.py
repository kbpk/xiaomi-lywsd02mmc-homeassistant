"""Battery helpers for CR2032-powered Xiaomi sensors."""

from __future__ import annotations

CR2032_EMPTY_VOLTAGE = 2.2
CR2032_FULL_VOLTAGE = 3.1


def estimate_cr2032_percentage(voltage: float) -> int:
    """Estimate a bounded percentage using the Xiaomi BLE CR2032 convention."""

    fraction = (voltage - CR2032_EMPTY_VOLTAGE) / (
        CR2032_FULL_VOLTAGE - CR2032_EMPTY_VOLTAGE
    )
    return round(min(1.0, max(0.0, fraction)) * 100)
