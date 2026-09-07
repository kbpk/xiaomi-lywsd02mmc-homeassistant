"""LYWSD02 family clock and direct-notification payload helpers."""

from __future__ import annotations

import math
import struct
from datetime import datetime


def build_time_payload(now: datetime) -> bytes:
    """Build Xiaomi's five-byte local-clock payload.

    The first four bytes contain UTC Unix time plus any sub-hour part of the
    current UTC offset. The signed final byte contains whole hours. This is the
    representation used by the clock firmware to support :30 and :45 zones.
    """

    offset = now.utcoffset()
    if now.tzinfo is None or offset is None:
        raise ValueError("time must be timezone-aware")
    offset_seconds = int(offset.total_seconds())
    hours = math.trunc(offset_seconds / 3600)
    if not -128 <= hours <= 127:
        raise ValueError("UTC offset does not fit the protocol byte")
    sub_hour_seconds = offset_seconds - hours * 3600
    epoch = int(now.timestamp()) + sub_hour_seconds
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("Unix timestamp does not fit uint32")
    return struct.pack("<Ib", epoch, hours)


def parse_time_payload(payload: bytes) -> tuple[int, int]:
    """Return the stored adjusted epoch and signed whole-hour offset."""

    if len(payload) != 5:
        raise ValueError("clock payload must contain exactly five bytes")
    epoch, offset = struct.unpack("<Ib", payload)
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
    """Decode the native three-byte EBE0CCC1 notification."""

    if len(payload) != 3:
        raise ValueError("temperature/humidity notification must be three bytes")
    temperature, humidity = struct.unpack("<hB", payload)
    if not 0 <= humidity <= 100:
        raise ValueError("humidity is outside the valid range")
    return temperature / 100, humidity


def unit_to_payload(unit: str) -> bytes:
    """Encode display unit: 0 = Celsius, 1 = Fahrenheit."""

    if unit == "celsius":
        return b"\x00"
    if unit == "fahrenheit":
        return b"\x01"
    raise ValueError(f"unsupported display unit: {unit}")


def payload_to_unit(payload: bytes) -> str:
    """Decode the display unit characteristic."""

    if payload == b"\x00":
        return "celsius"
    if payload == b"\x01":
        return "fahrenheit"
    raise ValueError("unsupported display unit value")
