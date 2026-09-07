"""Redacted diagnostics support."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_BINDKEY, CONF_DEVICE_ID, CONF_TOKEN
from .models import LYWSD02MMCConfigEntry

TO_REDACT = {CONF_BINDKEY, CONF_TOKEN, CONF_DEVICE_ID, "address", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LYWSD02MMCConfigEntry
) -> dict[str, Any]:
    """Return useful protocol state without credentials or stable identifiers."""

    state = entry.runtime_data.coordinator.data
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "metadata": {
            "product_id": entry.runtime_data.metadata.product_id,
            "model": entry.runtime_data.metadata.model,
            "revision": entry.runtime_data.metadata.revision,
            "firmware": entry.runtime_data.metadata.firmware,
            "hardware": entry.runtime_data.metadata.hardware,
            "supports_time": entry.runtime_data.metadata.supports_time,
            "supports_unit": entry.runtime_data.metadata.supports_unit,
        },
        "passive_state": {
            "available": state.available,
            "has_temperature": state.temperature is not None,
            "has_humidity": state.humidity is not None,
            "has_battery": state.battery is not None,
            "frame_counter": state.frame_counter,
            "last_seen": state.last_seen.isoformat() if state.last_seen else None,
        },
    }
