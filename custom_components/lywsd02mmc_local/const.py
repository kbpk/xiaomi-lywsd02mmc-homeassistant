"""Constants for the completely local LYWSD02MMC integration."""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "lywsd02mmc_local"

CONF_BINDKEY = "bindkey"
CONF_TOKEN = "token"
CONF_PRODUCT_ID = "product_id"
CONF_DEVICE_ID = "device_id"
CONF_FIRMWARE = "firmware"
CONF_HARDWARE = "hardware"
CONF_SUPPORTS_TIME = "supports_time"
CONF_SUPPORTS_UNIT = "supports_unit"
CONF_AUTO_SYNC = "auto_sync_clock"
CONF_METHOD = "method"

MIBEACON_SERVICE_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
MIBLE_CHAR_10 = "00000010-0000-1000-8000-00805f9b34fb"
MIBLE_CHAR_19 = "00000019-0000-1000-8000-00805f9b34fb"
MIBLE_SERVICE_UUID = MIBEACON_SERVICE_UUID

ENVIRONMENT_SERVICE_UUID = "ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6"
TIME_CHAR_UUID = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"
UNIT_CHAR_UUID = "ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6"
TEMPERATURE_HUMIDITY_CHAR_UUID = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"

MODEL_NUMBER_CHAR_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
FIRMWARE_REVISION_CHAR_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION_CHAR_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
SOFTWARE_REVISION_CHAR_UUID = "00002a28-0000-1000-8000-00805f9b34fb"
MANUFACTURER_NAME_CHAR_UUID = "00002a29-0000-1000-8000-00805f9b34fb"

ACTIVE_OPERATION_TIMEOUT = 90.0
PROTOCOL_STAGE_TIMEOUT = 20.0
# Xiaomi clocks normally advertise much more often. This deliberately tolerant
# fallback avoids flapping when scanners/proxies miss a run of advertisements.
FALLBACK_AVAILABILITY_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class ProductInfo:
    """A known Xiaomi product identifier."""

    model: str
    revision: str
    clock: bool
    time_offset_step_minutes: int = 60
    celsius_value: int = 0x00


PRODUCTS: dict[int, ProductInfo] = {
    0x045B: ProductInfo("LYWSD02", "t1", True, celsius_value=0xFF),
    0x16E4: ProductInfo("LYWSD02MMC", "o2", True),
    # Confirmed on miaomiaoce.sensor_ht.t8 firmware 2.0.1_0021: byte 4 of
    # EBE0CCB7 is a signed count of 15-minute UTC-offset units.
    0x2542: ProductInfo(
        "LYWSD02MMC", "t8", True, time_offset_step_minutes=15
    ),
}

SUPPORTED_LOCAL_NAME_PREFIXES = ("LYWSD02", "LYWSD02MMC")
