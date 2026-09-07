"""Strict parser for the LYWSD02 family subset of Xiaomi MiBeacon."""

from __future__ import annotations

import logging
import struct
from collections import deque
from dataclasses import dataclass, field

from .const import PRODUCTS, ProductInfo
from .crypto import aes_ccm_decrypt

_LOGGER = logging.getLogger(__name__)


class MiBeaconError(ValueError):
    """Base class for malformed or unsupported MiBeacon frames."""


class UnsupportedProductError(MiBeaconError):
    """The frame belongs to a different Xiaomi product."""


class InvalidAdvertisementError(MiBeaconError):
    """The frame structure is invalid."""


class MacMismatchError(InvalidAdvertisementError):
    """The embedded MAC does not match the advertisement source."""


class DuplicateFrameError(MiBeaconError):
    """The exact frame was already accepted recently."""


@dataclass(frozen=True, slots=True)
class MiBeaconHeader:
    """Decoded MiBeacon header fields."""

    frame_control: int
    version: int
    product_id: int
    frame_counter: int
    registered: bool
    solicited: bool
    encrypted: bool
    includes_objects: bool
    includes_mac: bool
    product: ProductInfo
    mac: bytes
    payload_offset: int
    extended_counter: bytes | None = None


@dataclass(frozen=True, slots=True)
class MiBeaconReading:
    """One authenticated MiBeacon advertisement."""

    header: MiBeaconHeader
    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None
    object_ids: tuple[int, ...] = field(default_factory=tuple)


def address_to_bytes(address: str) -> bytes:
    """Convert a conventional Bluetooth address to six bytes."""

    compact = address.replace(":", "").replace("-", "")
    if len(compact) != 12:
        raise ValueError("Bluetooth address is not a six-byte MAC address")
    try:
        return bytes.fromhex(compact)
    except ValueError as err:
        raise ValueError("Bluetooth address is not hexadecimal") from err


def identify_service_data(
    data: bytes, address: str, *, validate_mac: bool = True
) -> MiBeaconHeader:
    """Parse and validate enough of a MiBeacon header for safe recognition."""

    if len(data) < 5:
        raise InvalidAdvertisementError("MiBeacon header is truncated")
    frame_control = int.from_bytes(data[0:2], "little")
    version = frame_control >> 12
    if version < 2:
        raise InvalidAdvertisementError(f"unsupported MiBeacon version {version}")
    if frame_control & 0x0080:
        raise InvalidAdvertisementError("mesh MiBeacon frames are not supported")

    product_id = int.from_bytes(data[2:4], "little")
    try:
        product = PRODUCTS[product_id]
    except KeyError as err:
        raise UnsupportedProductError(
            f"unsupported Xiaomi product id 0x{product_id:04x}"
        ) from err

    includes_mac = bool(frame_control & 0x0010)
    offset = 5
    source_mac = address_to_bytes(address)
    mac = source_mac
    if includes_mac:
        if len(data) < offset + 6:
            raise InvalidAdvertisementError("embedded MiBeacon MAC is truncated")
        mac = data[offset : offset + 6][::-1]
        offset += 6
        if validate_mac and mac != source_mac:
            raise MacMismatchError(
                f"embedded MAC {mac.hex(':')} does not match source {address}"
            )

    if frame_control & 0x0020:  # capability byte
        if len(data) <= offset:
            raise InvalidAdvertisementError("MiBeacon capability is truncated")
        capability = data[offset]
        offset += 1
        if capability & 0x20:  # IO capability follows
            if len(data) <= offset:
                raise InvalidAdvertisementError("MiBeacon IO capability is truncated")
            offset += 1

    return MiBeaconHeader(
        frame_control=frame_control,
        version=version,
        product_id=product_id,
        frame_counter=data[4],
        registered=bool(frame_control & 0x0100),
        solicited=bool(frame_control & 0x0200),
        encrypted=bool(frame_control & 0x0008),
        includes_objects=bool(frame_control & 0x0040),
        includes_mac=includes_mac,
        product=product,
        mac=mac,
        payload_offset=offset,
    )


def decrypt_payload(data: bytes, header: MiBeaconHeader, bindkey: bytes) -> bytes:
    """Authenticate and decrypt a MiBeacon v4/v5 payload.

    Nonce = embedded-MAC-in-advertisement-order || product-id+frame-counter ||
    three-byte extended counter. AAD is the single byte 0x11.
    """

    if not header.encrypted:
        return data[header.payload_offset :]
    if header.version not in (4, 5):
        raise InvalidAdvertisementError(
            f"encrypted MiBeacon v{header.version} is unsupported"
        )
    if len(data) < header.payload_offset + 9:
        raise InvalidAdvertisementError("encrypted MiBeacon payload is truncated")
    extended_counter = data[-7:-4]
    nonce = header.mac[::-1] + data[2:5] + extended_counter
    ciphertext = data[header.payload_offset : -7]
    tag = data[-4:]
    return aes_ccm_decrypt(bindkey, nonce, ciphertext, tag, b"\x11")


def _parse_objects(payload: bytes) -> tuple[dict[str, float | int], tuple[int, ...]]:
    values: dict[str, float | int] = {}
    object_ids: list[int] = []
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 3:
            raise InvalidAdvertisementError("MiBeacon object header is truncated")
        object_id = int.from_bytes(payload[offset : offset + 2], "little")
        length = payload[offset + 2]
        offset += 3
        if len(payload) - offset < length:
            raise InvalidAdvertisementError(
                f"MiBeacon object 0x{object_id:04x} is truncated"
            )
        value = payload[offset : offset + length]
        offset += length
        object_ids.append(object_id)

        if object_id == 0x1004 and length == 2:
            values["temperature"] = int.from_bytes(value, "little", signed=True) / 10
        elif object_id == 0x1006 and length == 2:
            values["humidity"] = int.from_bytes(value, "little") / 10
        elif object_id == 0x100A and length == 1:
            values["battery"] = value[0]
        elif object_id == 0x100D and length == 4:
            temperature, humidity = struct.unpack("<hH", value)
            values["temperature"] = temperature / 10
            values["humidity"] = humidity / 10
        elif object_id in (0x4801, 0x4C01) and length == 4:
            values["temperature"] = round(struct.unpack("<f", value)[0], 2)
        elif object_id in (0x4802, 0x4C02) and length == 1:
            values["humidity"] = value[0]
        elif object_id in (0x4808, 0x4C08) and length == 4:
            values["humidity"] = round(struct.unpack("<f", value)[0], 2)
        elif object_id in (0x4803, 0x4C03) and length == 1:
            values["battery"] = value[0]
        else:
            _LOGGER.debug("Unsupported MiBeacon object id 0x%04x", object_id)

    temperature = values.get("temperature")
    humidity = values.get("humidity")
    battery = values.get("battery")
    if temperature is not None and not -50 <= float(temperature) <= 100:
        raise InvalidAdvertisementError("temperature is outside the valid range")
    if humidity is not None and not 0 <= float(humidity) <= 100:
        raise InvalidAdvertisementError("humidity is outside the valid range")
    if battery is not None and not 0 <= int(battery) <= 100:
        raise InvalidAdvertisementError("battery is outside the valid range")
    return values, tuple(object_ids)


def parse_service_data(
    data: bytes, address: str, bindkey: bytes, *, validate_mac: bool = True
) -> MiBeaconReading:
    """Parse one service-data value and reject unauthenticated plaintext."""

    header = identify_service_data(data, address, validate_mac=validate_mac)
    if not header.includes_objects:
        return MiBeaconReading(header=header)
    if not header.encrypted:
        raise InvalidAdvertisementError(
            "unauthenticated MiBeacon object payload is not accepted"
        )
    payload = decrypt_payload(data, header, bindkey)
    values, object_ids = _parse_objects(payload)
    return MiBeaconReading(
        header=header,
        temperature=(
            float(values["temperature"]) if "temperature" in values else None
        ),
        humidity=float(values["humidity"]) if "humidity" in values else None,
        battery=int(values["battery"]) if "battery" in values else None,
        object_ids=object_ids,
    )


class MiBeaconParser:
    """Per-device authenticated parser with replay/duplicate suppression."""

    def __init__(self, address: str, bindkey: bytes) -> None:
        if len(bindkey) != 16:
            raise ValueError("MiBeacon bindkey must be 16 bytes")
        self.address = address
        self.bindkey = bindkey
        self._recent: deque[bytes] = deque(maxlen=64)

    def parse(self, data: bytes) -> MiBeaconReading:
        """Parse one frame once; failed authentication is never remembered."""

        fingerprint = bytes(data)
        if fingerprint in self._recent:
            raise DuplicateFrameError("duplicate MiBeacon frame")
        reading = parse_service_data(data, self.address, self.bindkey)
        self._recent.append(fingerprint)
        return reading


__all__ = [
    "DuplicateFrameError",
    "InvalidAdvertisementError",
    "MacMismatchError",
    "MiBeaconHeader",
    "MiBeaconParser",
    "MiBeaconReading",
    "UnsupportedProductError",
    "identify_service_data",
    "parse_service_data",
]
