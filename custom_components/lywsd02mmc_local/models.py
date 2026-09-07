"""Data models shared by the integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .bluetooth import LYWSD02MMCConnectionManager
    from .coordinator import LYWSD02MMCCoordinator


@dataclass(frozen=True, slots=True)
class DeviceCredentials:
    """Credentials established by MiBLE registration."""

    token: bytes
    bindkey: bytes
    device_id: bytes


@dataclass(slots=True)
class DeviceMetadata:
    """Identification and optional GATT capabilities."""

    product_id: int | None = None
    model: str = "LYWSD02MMC"
    revision: str | None = None
    manufacturer: str = "Xiaomi / Mijia / Miaomiaoce"
    firmware: str | None = None
    hardware: str | None = None
    software: str | None = None
    supports_time: bool = False
    supports_unit: bool = False


@dataclass(slots=True)
class SensorState:
    """Latest accumulated passive state."""

    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None
    rssi: int | None = None
    frame_counter: int | None = None
    last_seen: datetime | None = None
    available: bool = False


@dataclass(slots=True)
class RuntimeData:
    """Runtime objects attached to a config entry."""

    coordinator: LYWSD02MMCCoordinator
    connection: LYWSD02MMCConnectionManager
    metadata: DeviceMetadata
    extra: dict[str, Any] = field(default_factory=dict)


if TYPE_CHECKING:
    type LYWSD02MMCConfigEntry = ConfigEntry[RuntimeData]
else:
    LYWSD02MMCConfigEntry = Any
