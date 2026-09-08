#!/usr/bin/env python3
"""Authenticate a fresh connection and exercise LYWSD02MMC GATT controls."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

try:
    from bleak import BleakClient
except ImportError:
    print("Missing dependency: install requirements-lab.txt", file=sys.stderr)
    raise SystemExit(2) from None

from _ble_lab import JsonlTrace, locate


def load_protocol() -> tuple[Any, Any, Any]:
    """Load reusable modules without importing Home Assistant."""

    root = Path(__file__).resolve().parents[1]
    package_path = root / "custom_components" / "lywsd02mmc_local"
    package = ModuleType("lywsd02mmc_control_core")
    package.__path__ = [str(package_path)]
    sys.modules[package.__name__] = package
    return (
        import_module("lywsd02mmc_control_core.provision"),
        import_module("lywsd02mmc_control_core.time"),
        import_module("lywsd02mmc_control_core.const"),
    )


def read_credentials(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    token = bytes.fromhex(payload["token"])
    if len(token) != 12:
        raise ValueError("credential file contains an invalid token")
    payload["token"] = token
    return payload


async def run(args: argparse.Namespace) -> None:
    provision, time_protocol, constants = load_protocol()
    credentials = read_credentials(args.credentials)
    address = str(credentials["address"])
    product = constants.PRODUCTS.get(credentials.get("product_id"))
    celsius_value = product.celsius_value if product else 0x00
    trace = JsonlTrace(args.trace)
    disconnected = asyncio.Event()
    try:
        device, _advertisement = await locate(address, args.scan_timeout, trace)

        def on_disconnect(_client: BleakClient) -> None:
            disconnected.set()

        async with BleakClient(
            device,
            timeout=args.connect_timeout,
            disconnected_callback=on_disconnect,
        ) as client:
            await provision.login(client, credentials["token"], disconnected)
            trace.write("fresh_connection_login_confirmed")
            print("Fresh-connection MiBLE login confirmed.")

            if args.sync_clock:
                now = datetime.now().astimezone()
                expected = time_protocol.build_time_payload(now)
                await client.write_gatt_char(
                    constants.TIME_CHAR_UUID, expected, response=True
                )
                actual = bytes(await client.read_gatt_char(constants.TIME_CHAR_UUID))
                time_protocol.validate_time_readback(expected, actual)
                epoch, offset = time_protocol.parse_time_payload(actual)
                trace.write(
                    "clock_sync_confirmed",
                    epoch=epoch,
                    offset_hours=offset,
                    readback_length=len(actual),
                )
                print(
                    "Clock synchronized and verified: "
                    f"offset={offset:+d}h readback={len(actual)} bytes"
                )

            if args.set_unit:
                expected_unit = time_protocol.unit_to_payload(
                    args.set_unit, celsius_value=celsius_value
                )
                await client.write_gatt_char(
                    constants.UNIT_CHAR_UUID, expected_unit, response=True
                )
                actual_unit = bytes(
                    await client.read_gatt_char(constants.UNIT_CHAR_UUID)
                )
                selected = time_protocol.payload_to_unit(actual_unit)
                if selected != args.set_unit:
                    raise ValueError("display unit readback does not match the write")
                trace.write("display_unit_confirmed", unit=selected)
                print(f"Display unit write/readback confirmed: {selected}")
    except Exception as error:
        trace.write("control_error", error=type(error).__name__, detail=str(error))
        raise
    finally:
        trace.close()
    print(f"Redacted control trace saved: {args.trace}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path("private/lywsd02mmc-secrets.json"),
    )
    parser.add_argument(
        "--trace", type=Path, default=Path("captures/lywsd02mmc-control.jsonl")
    )
    parser.add_argument("--scan-timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--sync-clock", action="store_true")
    parser.add_argument("--set-unit", choices=("celsius", "fahrenheit"))
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"lywsd02mmc_control: {error}", file=sys.stderr)
        raise SystemExit(1) from None
