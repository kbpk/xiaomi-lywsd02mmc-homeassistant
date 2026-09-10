"""Clock synchronization button."""

from __future__ import annotations

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LYWSD02MMCEntity
from .models import LYWSD02MMCConfigEntry, RuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LYWSD02MMCConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.metadata.supports_time:
        async_add_entities([LYWSD02MMCSyncClockButton(entry.runtime_data)])


class LYWSD02MMCSyncClockButton(LYWSD02MMCEntity, ButtonEntity):
    _attr_translation_key = "synchronize_clock"

    def __init__(self, runtime: RuntimeData) -> None:
        super().__init__(runtime, "synchronize_clock")

    @property
    @override
    def available(self) -> bool:
        # Pressing performs its own connectability check.
        return True

    @override
    async def async_press(self) -> None:
        try:
            assert self.runtime.clock_sync is not None
            await self.runtime.clock_sync.async_synchronize(reason="manual")
        except Exception as err:
            raise HomeAssistantError(
                translation_domain="lywsd02mmc_local",
                translation_key="clock_sync_failed",
            ) from err
