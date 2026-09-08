#!/usr/bin/env python3
"""Discover an LYWSD02MMC and capture a read-only GATT fingerprint."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

try:
    from bleak import BleakClient
except ImportError:
    print("Missing dependency: install requirements-lab.txt", file=sys.stderr)
    raise SystemExit(2) from None

from _ble_lab import JsonlTrace, locate, product_id, service_data

ENVIRONMENT_CHAR = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"
SAFE_READ_UUIDS = {
    "00002a24-0000-1000-8000-00805f9b34fb",  # model
    "00002a26-0000-1000-8000-00805f9b34fb",  # firmware
    "00002a27-0000-1000-8000-00805f9b34fb",  # hardware
    "00002a28-0000-1000-8000-00805f9b34fb",  # software
    "00002a29-0000-1000-8000-00805f9b34fb",  # manufacturer
    "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6",  # time
    "ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6",  # unit
}


async def run(args: argparse.Namespace) -> None:
    trace = JsonlTrace(args.output)
    try:
        trace.write("scan_start", requested_address=args.address)
        device, advertisement = await locate(args.address, args.scan_timeout, trace)
        pid = product_id(service_data(advertisement))
        print(f"Found {advertisement.local_name or device.name}: {device.address}")
        pid_text = f"0x{pid:04X}" if pid is not None else "unknown"
        print(f"MiBeacon product id: {pid_text}")
        trace.write("connect_start", address=device.address, product_id=pid)

        async with BleakClient(device, timeout=args.connect_timeout) as client:
            trace.write("connected", address=device.address)
            print("\nGATT database:")
            environment_characteristic: Any | None = None
            for service in client.services:
                print(f"service {service.uuid}  {service.description}")
                trace.write(
                    "service", uuid=service.uuid, description=service.description
                )
                for characteristic in service.characteristics:
                    uuid = characteristic.uuid.lower()
                    properties = list(characteristic.properties)
                    print(
                        f"  char {characteristic.uuid} "
                        f"handle=0x{characteristic.handle:04x} "
                        f"props={','.join(properties)}"
                    )
                    trace.write(
                        "characteristic",
                        service_uuid=service.uuid,
                        uuid=characteristic.uuid,
                        handle=characteristic.handle,
                        properties=properties,
                    )
                    if uuid == ENVIRONMENT_CHAR:
                        environment_characteristic = characteristic
                    if args.read and uuid in SAFE_READ_UUIDS and "read" in properties:
                        try:
                            value = bytes(await client.read_gatt_char(characteristic))
                        except Exception as error:
                            trace.write("read_error", uuid=uuid, error=repr(error))
                        else:
                            trace.write("read", uuid=uuid, value=value.hex())

            if environment_characteristic is not None and args.duration > 0:
                received = asyncio.Event()

                def notify(sender: Any, data: bytearray) -> None:
                    value = bytes(data)
                    trace.write(
                        "environment_notify", uuid=sender.uuid, value=value.hex()
                    )
                    if len(value) == 3:
                        raw_temperature = int.from_bytes(
                            value[:2], "little", signed=True
                        )
                        temperature = raw_temperature / 100
                        print(f"temperature={temperature:.2f} °C humidity={value[2]} %")
                    elif len(value) == 5:
                        raw_temperature = int.from_bytes(
                            value[:2], "little", signed=True
                        )
                        voltage_mv = int.from_bytes(value[3:5], "little")
                        print(
                            f"temperature={raw_temperature / 100:.2f} °C "
                            f"humidity={value[2]} % battery={voltage_mv} mV"
                        )
                    else:
                        print(f"environment notification: {value.hex(' ')}")
                    received.set()

                await client.start_notify(environment_characteristic, notify)
                print(f"\nWaiting up to {args.duration:g}s for environment data…")
                try:
                    await asyncio.wait_for(received.wait(), timeout=args.duration)
                except TimeoutError:
                    print("No environment notification received in the capture window.")
                finally:
                    await client.stop_notify(environment_characteristic)
            trace.write("capture_complete")
    except Exception as error:
        trace.write("capture_error", error=repr(error))
        raise
    finally:
        trace.close()
    print(f"Trace saved: {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address", help="target BLE address; otherwise detect by PID/name"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("captures/lywsd02mmc-gatt.jsonl")
    )
    parser.add_argument("--scan-timeout", type=float, default=25.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument(
        "--read", action="store_true", help="read only known non-secret characteristics"
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"lywsd02mmc_diag: {error}", file=sys.stderr)
        raise SystemExit(1) from None
