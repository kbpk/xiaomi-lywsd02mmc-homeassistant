#!/usr/bin/env python3
"""Provision local MiBLE credentials using a directly attached BLE adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

try:
    from bleak import BleakClient
except ImportError:
    print("Missing dependency: install requirements-lab.txt", file=sys.stderr)
    raise SystemExit(2) from None

from _ble_lab import JsonlTrace, locate, product_id, service_data


def load_protocol() -> tuple[Any, Any]:
    """Load reusable protocol modules without importing Home Assistant."""

    root = Path(__file__).resolve().parents[1]
    package_path = root / "custom_components" / "lywsd02mmc_local"
    package = ModuleType("lywsd02mmc_lab_core")
    package.__path__ = [str(package_path)]
    sys.modules[package.__name__] = package
    provision = import_module("lywsd02mmc_lab_core.provision")
    constants = import_module("lywsd02mmc_lab_core.const")
    return provision, constants


def save_credentials(path: Path, payload: dict[str, Any]) -> None:
    """Create a new mode-0600 credential file without overwriting one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    finally:
        if fd >= 0:
            os.close(fd)


class TracedClient:
    """Record operation metadata while redacting every protocol value."""

    def __init__(self, client: BleakClient, trace: JsonlTrace) -> None:
        self._client = client
        self._trace = trace

    async def start_notify(self, characteristic: Any, callback: Any) -> None:
        self._trace.write("subscribe", uuid=str(characteristic))
        await self._client.start_notify(characteristic, callback)

    async def stop_notify(self, characteristic: Any) -> None:
        self._trace.write("unsubscribe", uuid=str(characteristic))
        await self._client.stop_notify(characteristic)

    async def write_gatt_char(
        self, characteristic: Any, data: bytes, *, response: bool
    ) -> None:
        self._trace.write(
            "write", uuid=str(characteristic), length=len(data), content="redacted"
        )
        await self._client.write_gatt_char(characteristic, data, response=response)


async def run(args: argparse.Namespace) -> None:
    if not args.i_understand_rebinding:
        raise RuntimeError(
            "Refusing activation without --i-understand-rebinding; local activation "
            "can replace an existing Mi Home/Xiaomi binding"
        )
    provision, constants = load_protocol()
    trace = JsonlTrace(args.trace)
    disconnected = asyncio.Event()
    try:
        trace.write("scan_start", requested_address=args.address)
        device, advertisement = await locate(args.address, args.scan_timeout, trace)
        pid = product_id(service_data(advertisement))
        print(f"Found {advertisement.local_name or device.name}: {device.address}")
        pid_text = f"0x{pid:04X}" if pid is not None else "unknown"
        print(f"MiBeacon product id: {pid_text}")

        def on_disconnect(_client: BleakClient) -> None:
            disconnected.set()

        trace.write("connect_start", address=device.address, product_id=pid)
        async with BleakClient(
            device,
            timeout=args.connect_timeout,
            disconnected_callback=on_disconnect,
        ) as client:
            required = (constants.MIBLE_CHAR_10, constants.MIBLE_CHAR_19)
            missing = [
                uuid
                for uuid in required
                if client.services.get_characteristic(uuid) is None
            ]
            if missing:
                raise provision.UnsupportedMiBLEDeviceError(
                    "Missing MiBLE activation characteristics: " + ", ".join(missing)
                )
            trace.write("activation_start", product_id=pid)
            credentials = await provision.activate(
                TracedClient(client, trace), disconnected
            )
            trace.write("activation_and_login_confirmed")

        save_credentials(
            args.output,
            {
                "address": device.address,
                "product_id": pid,
                "token": credentials.token.hex(),
                "bindkey": credentials.bindkey.hex(),
                "device_id": credentials.device_id.hex(),
            },
        )
    except Exception as error:
        trace.write("activation_error", error=type(error).__name__, detail=str(error))
        raise
    finally:
        trace.close()
    print("Activation and subsequent MiBLE login confirmed.")
    print(f"Credentials saved (secret; do not publish): {args.output}")
    print(f"Redacted trace saved: {args.trace}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address", help="target BLE address; otherwise detect by PID/name"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("private/lywsd02mmc-secrets.json")
    )
    parser.add_argument(
        "--trace", type=Path, default=Path("captures/lywsd02mmc-activation.jsonl")
    )
    parser.add_argument("--scan-timeout", type=float, default=25.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument(
        "--i-understand-rebinding",
        action="store_true",
        help="confirm that activation can replace an existing Xiaomi/Mi Home binding",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"lywsd02mmc_activate: {error}", file=sys.stderr)
        raise SystemExit(1) from None
