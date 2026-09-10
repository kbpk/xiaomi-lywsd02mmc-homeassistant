"""Passive sensor entities."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LYWSD02MMCEntity
from .models import LYWSD02MMCConfigEntry, RuntimeData, SensorState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class LYWSD02MMCSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SensorState], float | int | None]


SENSORS = (
    LYWSD02MMCSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.temperature,
    ),
    LYWSD02MMCSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.humidity,
    ),
    LYWSD02MMCSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.battery,
    ),
    LYWSD02MMCSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.rssi,
    ),
    LYWSD02MMCSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=3,
        value_fn=lambda state: state.battery_voltage,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LYWSD02MMCConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        LYWSD02MMCSensor(entry.runtime_data, entry, description)
        for description in SENSORS
        if description.key != "battery_voltage"
        or entry.runtime_data.metadata.product_id == 0x2542
    )


class LYWSD02MMCSensor(LYWSD02MMCEntity, SensorEntity):
    entity_description: LYWSD02MMCSensorDescription

    def __init__(
        self,
        runtime: RuntimeData,
        entry: LYWSD02MMCConfigEntry,
        description: LYWSD02MMCSensorDescription,
    ) -> None:
        super().__init__(runtime, description.key)
        self.entry = entry
        self.entity_description = description

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (
            self.entity_description.key == "battery_voltage"
            and self.runtime.clock_sync is not None
        ):
            self.entry.async_create_background_task(
                self.hass,
                self._async_refresh_native_environment(),
                "LYWSD02MMC native battery voltage refresh",
            )

    async def _async_refresh_native_environment(self) -> None:
        try:
            await self.runtime.clock_sync.async_refresh_environment()
        except Exception as err:
            _LOGGER.debug(
                "Native battery voltage refresh failed: %s",
                type(err).__name__,
            )

    @property
    @override
    def native_value(self) -> float | int | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (
            self.entity_description.key != "battery"
            or self.coordinator.data.battery is None
        ):
            return None
        return {
            "source": (
                "voltage_estimate"
                if self.coordinator.data.battery_estimated
                else "mibeacon"
            )
        }
