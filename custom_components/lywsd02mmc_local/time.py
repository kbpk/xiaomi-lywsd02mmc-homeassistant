"""LYWSD02 family clock and direct-notification payload helpers."""

from __future__ import annotations

import math
import struct
from datetime import datetime


def build_time_payload(
    now: datetime, *, offset_step_minutes: int = 60
) -> bytes:
    """Build Xiaomi's five-byte local-clock payload.

    The first four bytes contain UTC Unix time. The signed final byte contains
    the current UTC offset in device-specific units. Original t1 firmware uses
    whole hours; confirmed t8 firmware uses 15-minute units. For legacy
    whole-hour firmware only, a sub-hour remainder is folded into the epoch as
    done by existing LYWSD02 clients.
    """

    offset = now.utcoffset()
    if now.tzinfo is None or offset is None:
        raise ValueError("time must be timezone-aware")
    if offset_step_minutes not in (15, 60):
        raise ValueError("time offset step must be 15 or 60 minutes")
    offset_seconds = int(offset.total_seconds())
    step_seconds = offset_step_minutes * 60
    offset_units = math.trunc(offset_seconds / step_seconds)
    if not -128 <= offset_units <= 127:
        raise ValueError("UTC offset does not fit the protocol byte")
    sub_hour_seconds = offset_seconds - offset_units * step_seconds
    if offset_step_minutes == 15 and sub_hour_seconds:
        raise ValueError("UTC offset is not a multiple of 15 minutes")
    epoch = int(now.timestamp()) + sub_hour_seconds
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("Unix timestamp does not fit uint32")
    return struct.pack("<Ib", epoch, offset_units)


def parse_time_payload(payload: bytes) -> tuple[int, int]:
    """Return the stored adjusted epoch and signed whole-hour offset."""

    if len(payload) < 5:
        raise ValueError("clock payload must contain at least five bytes")
    epoch, offset = struct.unpack("<Ib", payload[:5])
    return int(epoch), int(offset)


def validate_time_readback(
    expected: bytes, actual: bytes, *, tolerance_seconds: int = 5
) -> None:
    """Validate a clock readback while allowing normal elapsed seconds."""

    expected_epoch, expected_offset = parse_time_payload(expected)
    actual_epoch, actual_offset = parse_time_payload(actual)
    if actual_offset != expected_offset:
        raise ValueError("clock returned a different timezone offset")
    if abs(actual_epoch - expected_epoch) > tolerance_seconds:
        raise ValueError("clock returned a time outside the verification tolerance")


def parse_temperature_humidity_notification(payload: bytes) -> tuple[float, int]:
    """Decode a native EBE0CCC1 notification.

    Original devices send three bytes. Confirmed t8 firmware appends a
    little-endian two-byte battery voltage in millivolts.
    """

    if len(payload) not in (3, 5):
        raise ValueError("temperature/humidity notification must be 3 or 5 bytes")
    temperature, humidity = struct.unpack("<hB", payload[:3])
    if not 0 <= humidity <= 100:
        raise ValueError("humidity is outside the valid range")
    return temperature / 100, humidity


def unit_to_payload(unit: str, *, celsius_value: int = 0x00) -> bytes:
    """Encode display unit using the revision-specific Celsius value."""

    if unit == "celsius":
        if celsius_value not in (0x00, 0xFF):
            raise ValueError("unsupported Celsius protocol value")
        return bytes((celsius_value,))
    if unit == "fahrenheit":
        return b"\x01"
    raise ValueError(f"unsupported display unit: {unit}")


def payload_to_unit(payload: bytes) -> str:
    """Decode display unit; Celsius is 00 on t8 and FF on original t1."""

    if payload in (b"\x00", b"\xff"):
        return "celsius"
    if payload == b"\x01":
        return "fahrenheit"
    raise ValueError("unsupported display unit value")
