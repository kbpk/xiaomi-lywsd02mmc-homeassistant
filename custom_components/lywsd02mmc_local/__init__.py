"""Completely local Xiaomi LYWSD02MMC integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import LYWSD02MMCConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: LYWSD02MMCConfigEntry
) -> bool:
    """Set up passive listening and active-operation helpers."""

    from homeassistant.components import bluetooth
    from homeassistant.const import CONF_ADDRESS, Platform

    from .bluetooth import LYWSD02MMCConnectionManager
    from .const import (
        CONF_BINDKEY,
        CONF_PRODUCT_ID,
        CONF_TOKEN,
    )
    from .coordinator import LYWSD02MMCCoordinator
    from .models import DeviceMetadata, RuntimeData

    address: str = entry.data[CONF_ADDRESS]
    token_hex: str | None = entry.data.get(CONF_TOKEN)
    connection = LYWSD02MMCConnectionManager(
        hass,
        address,
        token=bytes.fromhex(token_hex) if token_hex else None,
        product_id=entry.data.get(CONF_PRODUCT_ID),
    )
    metadata = DeviceMetadata(
        product_id=entry.data.get(CONF_PRODUCT_ID),
        model=entry.data.get("model", "LYWSD02MMC"),
        revision=entry.data.get("revision"),
        manufacturer=entry.data.get("manufacturer", "Xiaomi / Mijia / Miaomiaoce"),
        firmware=entry.data.get("firmware"),
        hardware=entry.data.get("hardware"),
        software=entry.data.get("software"),
        supports_time=entry.data.get("supports_time", False),
        supports_unit=entry.data.get("supports_unit", False),
    )
    coordinator = LYWSD02MMCCoordinator(
        hass,
        entry,
        address,
        bytes.fromhex(entry.data[CONF_BINDKEY]),
        entry.data.get("display_unit"),
    )
    entry.runtime_data = RuntimeData(coordinator, connection, metadata)
    coordinator.async_start()

    # Process the most recent cached advertisement immediately after restart.
    if service_info := bluetooth.async_last_service_info(hass, address, False):
        coordinator._async_advertisement(
            service_info, bluetooth.BluetoothChange.ADVERTISEMENT
        )

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.SENSOR, Platform.BUTTON, Platform.SELECT]
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: LYWSD02MMCConfigEntry
) -> bool:
    """Unload all entities; callbacks are removed by config-entry unload hooks."""

    from homeassistant.const import Platform

    return await hass.config_entries.async_unload_platforms(
        entry, [Platform.SENSOR, Platform.BUTTON, Platform.SELECT]
    )
