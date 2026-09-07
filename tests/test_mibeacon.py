from __future__ import annotations

import struct

import pytest

from custom_components.lywsd02mmc_local.crypto import (
    AuthenticationTagError,
    aes_ccm_encrypt,
)
from custom_components.lywsd02mmc_local.mibeacon import (
    DuplicateFrameError,
    InvalidAdvertisementError,
    MacMismatchError,
    MiBeaconParser,
    parse_service_data,
)


def _object(object_id: int, value: bytes) -> bytes:
    return struct.pack("<HB", object_id, len(value)) + value


def _encrypted_frame(
    product_id: int,
    counter: int,
    mac: bytes,
    bindkey: bytes,
    payload: bytes,
    *,
    embedded_mac: bool = True,
    extended_counter: bytes = b"\x01\x02\x03",
) -> bytes:
    control = 0x5848 | (0x10 if embedded_mac else 0)
    header = struct.pack("<HHB", control, product_id, counter)
    if embedded_mac:
        header += mac[::-1]
    nonce = mac[::-1] + struct.pack("<HB", product_id, counter) + extended_counter
    ciphertext, tag = aes_ccm_encrypt(bindkey, nonce, payload, b"\x11")
    return header + ciphertext + extended_counter + tag


def test_real_16e4_encrypted_humidity_vector() -> None:
    reading = parse_service_data(
        bytes.fromhex("5858e4162c84535638c1a42b6ef2e91200006c884d9e"),
        "A4:C1:38:56:53:84",
        bytes.fromhex("a115210eed7a88e50ad52662e732a9fb"),
    )
    assert reading.header.product_id == 0x16E4
    assert reading.header.version == 5
    assert reading.header.frame_counter == 44
    assert reading.humidity == 58
    assert reading.object_ids == (0x4C02,)


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ("4858422529202d8c7a76b756a82a0078b8224e", 26.10),
        ("585842258878fd0e38c1a405f6538aa72a0062b1a966", 38.0),
    ],
)
def test_real_2542_revision_vectors(frame: str, expected: float) -> None:
    reading = parse_service_data(
        bytes.fromhex(frame),
        "A4:C1:38:0E:FD:78",
        bytes.fromhex("19b1c678ab0a8bc3dc77765f059188d4"),
    )
    assert reading.header.product_id == 0x2542
    assert expected in (reading.temperature, reading.humidity)


@pytest.mark.parametrize("product_id", [0x045B, 0x16E4, 0x2542])
def test_known_products_multiple_objects_and_battery(product_id: int) -> None:
    mac = bytes.fromhex("a4c138010203")
    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    payload = b"".join(
        (
            _object(0x1004, struct.pack("<h", 231)),
            _object(0x1006, struct.pack("<H", 456)),
            _object(0x100A, b"\x57"),
        )
    )
    reading = parse_service_data(
        _encrypted_frame(product_id, 7, mac, key, payload),
        "A4:C1:38:01:02:03",
        key,
    )
    assert reading.temperature == 23.1
    assert reading.humidity == 45.6
    assert reading.battery == 87


def test_newer_float_humidity_object() -> None:
    mac = bytes.fromhex("a4c138010203")
    key = b"k" * 16
    reading = parse_service_data(
        _encrypted_frame(
            0x2542,
            9,
            mac,
            key,
            _object(0x4C08, struct.pack("<f", 52.25)),
        ),
        "A4:C1:38:01:02:03",
        key,
    )
    assert reading.humidity == 52.25


def test_bad_key_and_corrupted_mic_are_rejected() -> None:
    mac = bytes.fromhex("a4c138010203")
    key = bytes.fromhex("00112233445566778899aabbccddeeff")
    frame = _encrypted_frame(0x2542, 8, mac, key, _object(0x4C03, b"\x63"))
    with pytest.raises(AuthenticationTagError):
        parse_service_data(frame, "A4:C1:38:01:02:03", b"x" * 16)
    damaged = frame[:-1] + bytes((frame[-1] ^ 0x80,))
    with pytest.raises(AuthenticationTagError):
        parse_service_data(damaged, "A4:C1:38:01:02:03", key)


def test_unauthenticated_plaintext_objects_are_rejected() -> None:
    mac = bytes.fromhex("a4c138010203")
    header = struct.pack("<HHB", 0x5850, 0x2542, 1) + mac[::-1]
    frame = header + _object(0x100A, b"\x55")
    with pytest.raises(InvalidAdvertisementError, match="unauthenticated"):
        parse_service_data(frame, "A4:C1:38:01:02:03", b"k" * 16)


def test_mac_ordering_and_mismatch() -> None:
    mac = bytes.fromhex("a4c138010203")
    key = b"k" * 16
    frame = _encrypted_frame(0x16E4, 3, mac, key, _object(0x4C02, b"\x2a"))
    assert parse_service_data(frame, "A4:C1:38:01:02:03", key).humidity == 42
    with pytest.raises(MacMismatchError):
        parse_service_data(frame, "A4:C1:38:01:02:04", key)


def test_duplicate_suppression_and_counter_wrap() -> None:
    mac = bytes.fromhex("a4c138010203")
    key = b"k" * 16
    parser = MiBeaconParser("A4:C1:38:01:02:03", key)
    first = _encrypted_frame(0x2542, 255, mac, key, _object(0x4C03, b"\x50"))
    wrapped = _encrypted_frame(0x2542, 0, mac, key, _object(0x4C03, b"\x4f"))
    assert parser.parse(first).battery == 80
    with pytest.raises(DuplicateFrameError):
        parser.parse(first)
    assert parser.parse(wrapped).battery == 79
