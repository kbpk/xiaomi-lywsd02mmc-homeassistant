"""Redacted diagnostics support."""

from __future__ import annotations

from datetime import UTC, datetime
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

    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    state = coordinator.data
    connection = runtime.connection.state
    clock = runtime.clock_sync.state if runtime.clock_sync is not None else None
    age = (
        max(0, int((datetime.now(UTC) - state.last_seen).total_seconds()))
        if state.last_seen
        else None
    )
    return {
        "config_entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "metadata": {
            "product_id": runtime.metadata.product_id,
            "model": runtime.metadata.model,
            "revision": runtime.metadata.revision,
            "firmware": runtime.metadata.firmware,
            "hardware": runtime.metadata.hardware,
            "software": runtime.metadata.software,
            "supports_time": runtime.metadata.supports_time,
            "supports_unit": runtime.metadata.supports_unit,
        },
        "passive_state": {
            "available": state.available,
            "has_temperature": state.temperature is not None,
            "has_humidity": state.humidity is not None,
            "has_battery": state.battery is not None,
            "battery_voltage": state.battery_voltage,
            "rssi": state.rssi,
            "frame_counter": state.frame_counter,
            "object_ids": [f"0x{value:04X}" for value in state.object_ids],
            "last_seen": state.last_seen.isoformat() if state.last_seen else None,
            "last_seen_age_seconds": age,
            "last_native_update": (
                state.last_native_update.isoformat()
                if state.last_native_update
                else None
            ),
            "bluetooth_source": coordinator.last_source,
            "source_connectable": coordinator.last_connectable,
        },
        "active_gatt": {
            "last_operation": connection.last_operation,
            "last_attempt": (
                connection.last_attempt.isoformat() if connection.last_attempt else None
            ),
            "last_success": (
                connection.last_success.isoformat() if connection.last_success else None
            ),
            "last_login": (
                connection.last_login.isoformat() if connection.last_login else None
            ),
            "last_error": connection.last_error,
        },
        "automatic_clock_sync": {
            "enabled": clock.enabled if clock else False,
            "last_reason": clock.last_reason if clock else None,
            "last_attempt": (
                clock.last_attempt.isoformat() if clock and clock.last_attempt else None
            ),
            "last_success": (
                clock.last_success.isoformat() if clock and clock.last_success else None
            ),
            "last_error": clock.last_error if clock else None,
            "last_synced_offset_minutes": (
                clock.last_synced_offset_minutes if clock else None
            ),
            "next_transition": (
                clock.next_transition.isoformat()
                if clock and clock.next_transition
                else None
            ),
        },
    }
