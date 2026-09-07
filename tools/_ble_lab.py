"""Shared Windows/Linux Bleak helpers for physical-device development tools."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from bleak import BleakScanner

MIBEACON_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
PRODUCT_IDS = {0x045B, 0x16E4, 0x2542}


class JsonlTrace:
    """Write an immediately flushed trace with restrictive Unix permissions."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        self._handle = os.fdopen(fd, "w", encoding="utf-8")
        self._started = time.monotonic()

    def write(self, event: str, **fields: Any) -> None:
        row = {
            "t": round(time.monotonic() - self._started, 6),
            "event": event,
            **fields,
        }
        self._handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def service_data(advertisement: Any) -> bytes | None:
    """Return FE95 service data without relying on UUID key casing."""

    for uuid, value in advertisement.service_data.items():
        if uuid.lower() == MIBEACON_UUID:
            return bytes(value)
    return None


def product_id(data: bytes | None) -> int | None:
    """Extract the MiBeacon product identifier."""

    return int.from_bytes(data[2:4], "little") if data and len(data) >= 4 else None


async def locate(
    address: str | None,
    scan_timeout: float,
    trace: JsonlTrace,
) -> tuple[Any, Any]:
    """Locate an exact address or a recognized LYWSD02 family advertisement."""

    chosen: tuple[Any, Any] | None = None
    found = asyncio.Event()

    def callback(device: Any, advertisement: Any) -> None:
        nonlocal chosen
        data = service_data(advertisement)
        pid = product_id(data)
        name = (advertisement.local_name or device.name or "").upper()
        address_match = bool(
            address and device.address.upper() == address.strip().upper()
        )
        model_match = pid in PRODUCT_IDS or name.startswith("LYWSD02")
        if not address_match and not model_match:
            return
        trace.write(
            "advertisement",
            address=device.address,
            name=advertisement.local_name or device.name,
            rssi=advertisement.rssi,
            product_id=pid,
            service_uuids=list(advertisement.service_uuids),
            service_data={
                uuid: bytes(value).hex()
                for uuid, value in advertisement.service_data.items()
            },
        )
        if address_match or (address is None and chosen is None):
            chosen = device, advertisement
            found.set()

    async with BleakScanner(detection_callback=callback):
        try:
            async with asyncio.timeout(scan_timeout):
                await found.wait()
        except TimeoutError:
            pass
    if chosen is None:
        target = address or "a recognized LYWSD02/LYWSD02MMC"
        raise RuntimeError(f"Did not find {target}; wake/reset the clock and retry")
    return chosen
