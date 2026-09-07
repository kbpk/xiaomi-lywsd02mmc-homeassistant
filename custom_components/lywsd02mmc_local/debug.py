"""Secret-safe helpers for real-device GATT inspection."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def log_gatt_fingerprint(client: Any) -> None:
    """Log UUIDs, handles and properties; never values or credentials."""

    for service in client.services:
        _LOGGER.debug("GATT service %s", service.uuid)
        for characteristic in service.characteristics:
            _LOGGER.debug(
                "GATT characteristic %s handle=%s properties=%s",
                characteristic.uuid,
                characteristic.handle,
                ",".join(characteristic.properties),
            )
