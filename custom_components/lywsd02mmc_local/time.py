"""LYWSD02 family clock and direct-notification payload helpers."""

from __future__ import annotations

import struct
from datetime import datetime


def build_time_payload(now: datetime) -> bytes:
    """Build Xiaomi's five-byte local-clock payload.

    The first four bytes contain UTC Unix time, adjusted only by any fractional
    part of the timezone offset. The signed final byte contains the whole-hour
    UTC offset. The fractional adjustment is the convention used by existing
    LYWSD02 clients for half-hour and quarter-hour zones.
    """

    offset = now.utcoffset()
    if now.tzinfo is None or offset is None:
        raise ValueError("time must be timezone-aware")
    offset_seconds = int(offset.total_seconds())
    offset_hours = int(offset_seconds / 3600)
    if not -128 <= offset_hours <= 127:
        raise ValueError("UTC offset does not fit the protocol byte")
    fractional_offset_seconds = offset_seconds - offset_hours * 3600
    epoch = int(now.timestamp()) + fractional_offset_seconds
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("Unix timestamp does not fit uint32")
    return struct.pack("<Ib", epoch, offset_hours)


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
