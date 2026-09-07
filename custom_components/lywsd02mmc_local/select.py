"""Physical temperature display unit select."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import LYWSD02MMCEntity
from .models import LYWSD02MMCConfigEntry, RuntimeData

OPTIONS = ["celsius", "fahrenheit"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LYWSD02MMCConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.metadata.supports_unit:
        async_add_entities([LYWSD02MMCUnitSelect(entry.runtime_data, entry)])


class LYWSD02MMCUnitSelect(LYWSD02MMCEntity, SelectEntity):
    _attr_translation_key = "display_unit"
    _attr_options = OPTIONS

    def __init__(self, runtime: RuntimeData, entry: LYWSD02MMCConfigEntry) -> None:
        super().__init__(runtime, "display_unit")
        self.entry = entry

    @property
    @override
    def available(self) -> bool:
        # A user action can reconnect even between passive measurement packets.
        return True

    @property
    @override
    def current_option(self) -> str | None:
        return self.coordinator.display_unit

    @override
    async def async_select_option(self, option: str) -> None:
        try:
            selected = await self.runtime.connection.async_set_unit(option)
        except Exception as err:
            raise HomeAssistantError(
                translation_domain="lywsd02mmc_local",
                translation_key="unit_change_failed",
            ) from err
        self.coordinator.display_unit = selected
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, "display_unit": selected}
        )
        self.async_write_ha_state()
