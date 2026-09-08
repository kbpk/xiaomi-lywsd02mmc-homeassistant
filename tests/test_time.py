from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.lywsd02mmc_local.time import (
    build_time_payload,
    parse_temperature_humidity_notification,
    parse_time_payload,
    payload_to_unit,
    unit_to_payload,
    validate_time_readback,
)


def test_utc_payload() -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    epoch, hours = parse_time_payload(build_time_payload(now))
    assert epoch == int(now.timestamp())
    assert hours == 0


@pytest.mark.parametrize(
    ("offset", "whole_hours", "remainder"),
    [
        (timedelta(hours=5, minutes=30), 5, 1800),
        (timedelta(hours=5, minutes=45), 5, 2700),
        (-timedelta(hours=3, minutes=30), -3, -1800),
        (-timedelta(hours=9, minutes=45), -9, -2700),
    ],
)
def test_fractional_timezones(
    offset: timedelta, whole_hours: int, remainder: int
) -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(offset))
    epoch, hours = parse_time_payload(build_time_payload(now))
    assert epoch == int(now.timestamp()) + remainder
    assert hours == whole_hours


def test_ha_configured_timezone_and_dst_transition() -> None:
    warsaw = ZoneInfo("Europe/Warsaw")
    before = datetime(2026, 3, 29, 1, 30, tzinfo=warsaw)
    after = datetime(2026, 3, 29, 3, 30, tzinfo=warsaw)
    assert parse_time_payload(build_time_payload(before))[1] == 1
    assert parse_time_payload(build_time_payload(after))[1] == 2


def test_t8_quarter_hour_timezone_encoding() -> None:
    warsaw = ZoneInfo("Europe/Warsaw")
    summer = datetime(2026, 9, 8, 1, 30, tzinfo=warsaw)
    epoch, offset_units = parse_time_payload(
        build_time_payload(summer, offset_step_minutes=15)
    )
    assert epoch == int(summer.timestamp())
    assert offset_units == 8

    nepal = datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=5, minutes=45)))
    assert parse_time_payload(
        build_time_payload(nepal, offset_step_minutes=15)
    )[1] == 23

    newfoundland = datetime(
        2026, 1, 2, tzinfo=timezone(-timedelta(hours=3, minutes=30))
    )
    assert parse_time_payload(
        build_time_payload(newfoundland, offset_step_minutes=15)
    )[1] == -14


def test_direct_notification_and_units() -> None:
    assert parse_temperature_humidity_notification(bytes.fromhex("c30a36")) == (
        27.55,
        54,
    )
    # Captured from physical PID 0x2542, firmware 2.0.1_0021. The final
    # uint16 is battery voltage (2804 mV) and is intentionally not part of
    # the temperature/humidity return type.
    assert parse_temperature_humidity_notification(
        bytes.fromhex("5b0b34f40a")
    ) == (29.07, 52)
    assert unit_to_payload("celsius") == b"\x00"
    assert unit_to_payload("celsius", celsius_value=0xFF) == b"\xff"
    assert unit_to_payload("fahrenheit") == b"\x01"
    assert payload_to_unit(b"\x00") == "celsius"
    assert payload_to_unit(b"\x01") == "fahrenheit"
    with pytest.raises(ValueError):
        payload_to_unit(b"\x02")


def test_clock_readback_validation() -> None:
    expected = bytes.fromhex("00f1536501")
    validate_time_readback(expected, bytes.fromhex("04f1536501"))
    validate_time_readback(expected, bytes.fromhex("04f15365010000"))
    with pytest.raises(ValueError, match="timezone"):
        validate_time_readback(expected, bytes.fromhex("00f1536502"))
    with pytest.raises(ValueError, match="tolerance"):
        validate_time_readback(expected, bytes.fromhex("10f1536501"))
