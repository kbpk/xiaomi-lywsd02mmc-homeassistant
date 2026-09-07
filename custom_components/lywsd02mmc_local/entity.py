"""Shared entity base for one LYWSD02MMC clock."""

from __future__ import annotations

from typing import override

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LYWSD02MMCCoordinator
from .models import RuntimeData


class LYWSD02MMCEntity(CoordinatorEntity[LYWSD02MMCCoordinator]):
    """Common identity and availability for entities of the physical device."""

    _attr_has_entity_name = True

    def __init__(self, runtime: RuntimeData, key: str) -> None:
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.connection.address}_{key}"

    @property
    @override
    def available(self) -> bool:
        return self.coordinator.data.available

    @property
    @override
    def device_info(self) -> DeviceInfo:
        metadata = self.runtime.metadata
        return DeviceInfo(
            identifiers={(DOMAIN, self.runtime.connection.address)},
            connections={(dr.CONNECTION_BLUETOOTH, self.runtime.connection.address)},
            manufacturer=metadata.manufacturer,
            model=metadata.model,
            model_id=(
                f"0x{metadata.product_id:04X}"
                if metadata.product_id is not None
                else None
            ),
            hw_version=metadata.hardware or metadata.revision,
            sw_version=metadata.firmware or metadata.software,
            name=f"{metadata.model} {self.runtime.connection.address[-5:]}",
        )
