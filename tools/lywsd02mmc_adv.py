#!/usr/bin/env python3
"""Capture and authenticate passive LYWSD02MMC MiBeacon advertisements."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

try:
    from bleak import BleakScanner
except ImportError:
    print("Missing dependency: install requirements-lab.txt", file=sys.stderr)
    raise SystemExit(2) from None

from _ble_lab import JsonlTrace, product_id, service_data


def load_parser() -> tuple[Any, Any]:
    """Load protocol modules without importing Home Assistant."""

    root = Path(__file__).resolve().parents[1]
    package_path = root / "custom_components" / "lywsd02mmc_local"
    package = ModuleType("lywsd02mmc_adv_core")
    package.__path__ = [str(package_path)]
    sys.modules[package.__name__] = package
    return (
        import_module("lywsd02mmc_adv_core.mibeacon"),
        import_module("lywsd02mmc_adv_core.crypto"),
    )


def read_credentials(path: Path) -> tuple[str, bytes]:
    """Read and validate only the fields needed for passive decryption."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    address = str(payload["address"])
    bindkey = bytes.fromhex(payload["bindkey"])
    if len(bindkey) != 16:
        raise ValueError("credential file contains an invalid bindkey")
    return address, bindkey


async def run(args: argparse.Namespace) -> None:
    mibeacon, crypto = load_parser()
    address, bindkey = read_credentials(args.credentials)
    expected_address = (args.address or address).upper()
    parser = mibeacon.MiBeaconParser(expected_address, bindkey)
    trace = JsonlTrace(args.output)
    valid_frames = 0

    def callback(device: Any, advertisement: Any) -> None:
        nonlocal valid_frames
        if device.address.upper() != expected_address:
            return
        data = service_data(advertisement)
        if data is None:
            return
        common = {
            "address": device.address,
            "rssi": advertisement.rssi,
            "product_id": product_id(data),
            "frame": data.hex(),
        }
        try:
            reading = parser.parse(data)
        except mibeacon.DuplicateFrameError:
            return
        except crypto.AuthenticationTagError:
            trace.write("invalid_authentication_tag", **common)
            print("Rejected advertisement with an invalid authentication tag.")
            return
        except mibeacon.MiBeaconError as error:
            trace.write("parse_error", error=type(error).__name__, **common)
            print(f"Ignored MiBeacon frame: {error}")
            return
        valid_frames += 1
        trace.write(
            "valid_mibeacon",
            **common,
            counter=reading.header.frame_counter,
            encrypted=reading.header.encrypted,
            registered=reading.header.registered,
            object_ids=[f"0x{item:04x}" for item in reading.object_ids],
            temperature=reading.temperature,
            humidity=reading.humidity,
            battery=reading.battery,
        )
        values = [
            f"counter={reading.header.frame_counter}",
            f"objects={','.join(f'0x{x:04x}' for x in reading.object_ids) or '-'}",
        ]
        if reading.temperature is not None:
            values.append(f"temperature={reading.temperature:.2f} °C")
        if reading.humidity is not None:
            values.append(f"humidity={reading.humidity:g} %")
        if reading.battery is not None:
            values.append(f"battery={reading.battery} %")
        print("valid MiBeacon: " + " ".join(values))

    try:
        trace.write("scan_start", address=expected_address)
        async with BleakScanner(detection_callback=callback):
            await asyncio.sleep(args.duration)
        trace.write("scan_complete", valid_frames=valid_frames)
    finally:
        trace.close()
    print(f"Accepted {valid_frames} unique authenticated/structural frame(s).")
    print(f"Trace saved: {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path("private/lywsd02mmc-secrets.json"),
    )
    parser.add_argument("--address", help="override target address")
    parser.add_argument(
        "--output", type=Path, default=Path("captures/lywsd02mmc-adv.jsonl")
    )
    parser.add_argument("--duration", type=float, default=60.0)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"lywsd02mmc_adv: {error}", file=sys.stderr)
        raise SystemExit(1) from None
