"""Passive sensor entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

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
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LYWSD02MMCEntity
from .models import LYWSD02MMCConfigEntry, RuntimeData, SensorState


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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LYWSD02MMCConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        LYWSD02MMCSensor(entry.runtime_data, description)
        for description in SENSORS
    )


class LYWSD02MMCSensor(LYWSD02MMCEntity, SensorEntity):
    entity_description: LYWSD02MMCSensorDescription

    def __init__(
        self, runtime: RuntimeData, description: LYWSD02MMCSensorDescription
    ) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> float | int | None:
        return self.entity_description.value_fn(self.coordinator.data)
