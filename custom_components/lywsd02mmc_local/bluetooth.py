"""Home Assistant Bluetooth discovery and short-lived GATT operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    ACTIVE_OPERATION_TIMEOUT,
    ENVIRONMENT_SERVICE_UUID,
    FIRMWARE_REVISION_CHAR_UUID,
    HARDWARE_REVISION_CHAR_UUID,
    MANUFACTURER_NAME_CHAR_UUID,
    MIBLE_CHAR_10,
    MIBLE_CHAR_19,
    MODEL_NUMBER_CHAR_UUID,
    PRODUCTS,
    SOFTWARE_REVISION_CHAR_UUID,
    TEMPERATURE_HUMIDITY_CHAR_UUID,
    TIME_CHAR_UUID,
    UNIT_CHAR_UUID,
)
from .debug import log_gatt_fingerprint
from .models import ConnectionState, DeviceCredentials, DeviceMetadata
from .provision import (
    UnsupportedMiBLEDeviceError,
    activate,
    login,
)
from .time import (
    EnvironmentReading,
    build_time_payload,
    parse_environment_notification,
    payload_to_unit,
    unit_to_payload,
    validate_time_readback,
)

_LOGGER = logging.getLogger(__name__)


class DeviceNotFoundError(BleakError):
    """No connectable device is currently known to Home Assistant."""


def _has_characteristic(client: BleakClient, uuid: str) -> bool:
    return client.services.get_characteristic(uuid) is not None


async def _read_text(client: BleakClient, uuid: str) -> str | None:
    if not _has_characteristic(client, uuid):
        return None
    try:
        return (
            bytes(await client.read_gatt_char(uuid))
            .decode("utf-8", errors="replace")
            .rstrip("\x00")
        )
    except BleakError:
        _LOGGER.debug("Could not read device information %s", uuid, exc_info=True)
        return None


async def read_metadata(
    client: BleakClient,
    product_id: int | None,
    *,
    require_mible: bool = False,
) -> DeviceMetadata:
    """Read GATT fingerprint, capabilities and Device Information values."""

    log_gatt_fingerprint(client)
    has_mible = _has_characteristic(client, MIBLE_CHAR_10) and _has_characteristic(
        client, MIBLE_CHAR_19
    )
    has_environment = client.services.get_service(ENVIRONMENT_SERVICE_UUID) is not None
    if require_mible and not has_mible:
        raise UnsupportedMiBLEDeviceError(
            "FE95 characteristics 0x0010 and 0x0019 are missing"
        )
    if not has_environment:
        raise UnsupportedMiBLEDeviceError("LYWSD02 environment service is missing")

    known = PRODUCTS.get(product_id) if product_id is not None else None
    model = await _read_text(client, MODEL_NUMBER_CHAR_UUID)
    metadata = DeviceMetadata(
        product_id=product_id,
        model=model or (known.model if known else "LYWSD02MMC"),
        revision=known.revision if known else None,
        firmware=await _read_text(client, FIRMWARE_REVISION_CHAR_UUID),
        hardware=await _read_text(client, HARDWARE_REVISION_CHAR_UUID),
        software=await _read_text(client, SOFTWARE_REVISION_CHAR_UUID),
        manufacturer=(
            await _read_text(client, MANUFACTURER_NAME_CHAR_UUID)
            or "Xiaomi / Mijia / Miaomiaoce"
        ),
        supports_time=_has_characteristic(client, TIME_CHAR_UUID),
        supports_unit=_has_characteristic(client, UNIT_CHAR_UUID),
    )
    _LOGGER.debug(
        "GATT fingerprint: model=%s firmware=%s hardware=%s time=%s unit=%s",
        metadata.model,
        metadata.firmware,
        metadata.hardware,
        metadata.supports_time,
        metadata.supports_unit,
    )
    return metadata


async def _write_clock(client: BleakClient, payload: bytes) -> bytes:
    """Write and, where supported, verify the common clock payload."""

    if not _has_characteristic(client, TIME_CHAR_UUID):
        raise UnsupportedMiBLEDeviceError("time characteristic is unavailable")
    await client.write_gatt_char(TIME_CHAR_UUID, payload, response=True)
    try:
        readback = bytes(await client.read_gatt_char(TIME_CHAR_UUID))
    except BleakError:
        return payload
    validate_time_readback(payload, readback)
    return readback


async def _read_environment_notification(
    client: BleakClient,
) -> EnvironmentReading:
    """Capture one native environment notification and always stop notify."""

    if not _has_characteristic(client, TEMPERATURE_HUMIDITY_CHAR_UUID):
        raise UnsupportedMiBLEDeviceError(
            "temperature/humidity characteristic is unavailable"
        )
    event = asyncio.Event()
    result: EnvironmentReading | None = None

    def notification(_characteristic: Any, data: bytearray) -> None:
        nonlocal result
        try:
            result = parse_environment_notification(bytes(data))
        except ValueError:
            _LOGGER.debug("Malformed EBE0CCC1 notification")
            return
        event.set()

    await client.start_notify(TEMPERATURE_HUMIDITY_CHAR_UUID, notification)
    try:
        async with asyncio.timeout(10):
            await event.wait()
    finally:
        try:
            await client.stop_notify(TEMPERATURE_HUMIDITY_CHAR_UUID)
        except BleakError:
            _LOGGER.debug("Could not stop EBE0CCC1 notification", exc_info=True)
    assert result is not None
    return result


class LYWSD02MMCConnectionManager:
    """Serialize active operations and disconnect after every operation."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        *,
        token: bytes | None = None,
        product_id: int | None = None,
    ) -> None:
        self.hass = hass
        self.address = address
        self.token = token
        self.product_id = product_id
        self.lock = asyncio.Lock()
        self._disconnected_event: asyncio.Event | None = None
        self.state = ConnectionState()

    @property
    def _product(self):
        """Return revision behavior when the MiBeacon PID is known."""

        return PRODUCTS.get(self.product_id) if self.product_id is not None else None

    def _time_payload(self, now: datetime) -> bytes:
        return build_time_payload(now)

    def _unit_payload(self, unit: str) -> bytes:
        celsius_value = self._product.celsius_value if self._product else 0x00
        return unit_to_payload(unit, celsius_value=celsius_value)

    async def _connect(self) -> BleakClient:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise DeviceNotFoundError(
                f"No connectable BLE route to {self.address} is available"
            )
        assert isinstance(ble_device, BLEDevice)
        disconnected_event = asyncio.Event()
        self._disconnected_event = disconnected_event

        def disconnected(_client: BleakClient) -> None:
            self.hass.loop.call_soon_threadsafe(disconnected_event.set)

        return await establish_connection(
            BleakClient,
            ble_device,
            self.address,
            disconnected_callback=disconnected,
        )

    async def _run(
        self,
        operation: Callable[[BleakClient], Any],
        *,
        authenticate: bool,
        operation_name: str,
    ) -> Any:
        async with self.lock:
            client: BleakClient | None = None
            self.state.last_operation = operation_name
            self.state.last_attempt = datetime.now(UTC)
            self.state.last_error = None
            try:
                async with asyncio.timeout(ACTIVE_OPERATION_TIMEOUT):
                    client = await self._connect()
                    if authenticate and self.token is not None:
                        if not (
                            _has_characteristic(client, MIBLE_CHAR_10)
                            and _has_characteristic(client, MIBLE_CHAR_19)
                        ):
                            raise UnsupportedMiBLEDeviceError(
                                "MiBLE login characteristics are unavailable"
                            )
                        await login(client, self.token, self._disconnected_event)
                        self.state.last_login = datetime.now(UTC)
                    result = await operation(client)
                    self.state.last_success = datetime.now(UTC)
                    return result
            except Exception as err:
                self.state.last_error = type(err).__name__
                raise
            finally:
                if client is not None and client.is_connected:
                    try:
                        async with asyncio.timeout(10):
                            await client.disconnect()
                    except Exception:
                        _LOGGER.debug("BLE disconnect failed", exc_info=True)

    async def async_activate(
        self,
    ) -> tuple[DeviceCredentials, DeviceMetadata, str | None]:
        """Activate, verify login and inspect capabilities."""

        async def operation(
            client: BleakClient,
        ) -> tuple[DeviceCredentials, DeviceMetadata, str | None]:
            metadata = await read_metadata(client, self.product_id, require_mible=True)
            credentials = await activate(client, self._disconnected_event)
            display_unit: str | None = None
            if metadata.supports_unit:
                try:
                    display_unit = payload_to_unit(
                        bytes(await client.read_gatt_char(UNIT_CHAR_UUID))
                    )
                except (BleakError, ValueError):
                    _LOGGER.debug("Could not read display unit", exc_info=True)
            return credentials, metadata, display_unit

        return await self._run(
            operation, authenticate=False, operation_name="activation"
        )

    async def async_probe(self) -> tuple[DeviceMetadata, str | None]:
        """Validate the GATT fingerprint without changing device credentials."""

        async def operation(client: BleakClient) -> tuple[DeviceMetadata, str | None]:
            metadata = await read_metadata(client, self.product_id, require_mible=False)
            display_unit = None
            if metadata.supports_unit:
                try:
                    display_unit = payload_to_unit(
                        bytes(await client.read_gatt_char(UNIT_CHAR_UUID))
                    )
                except (BleakError, ValueError):
                    _LOGGER.debug("Could not read display unit", exc_info=True)
            return metadata, display_unit

        return await self._run(
            operation,
            authenticate=self.token is not None,
            operation_name="probe",
        )

    async def async_sync_clock(self, now: datetime) -> bytes:
        """Authenticate, write current time, and verify by reading when possible."""

        payload = self._time_payload(now)

        async def operation(client: BleakClient) -> bytes:
            return await _write_clock(client, payload)

        return await self._run(
            operation, authenticate=True, operation_name="clock_sync"
        )

    async def async_refresh(
        self, now: datetime, *, sync_clock: bool
    ) -> EnvironmentReading | None:
        """Optionally sync the clock and capture native data in one connection."""

        async def operation(client: BleakClient) -> EnvironmentReading | None:
            if sync_clock:
                await _write_clock(client, self._time_payload(now))
            try:
                return await _read_environment_notification(client)
            except (BleakError, TimeoutError, ValueError):
                _LOGGER.debug(
                    "Could not capture native environment notification",
                    exc_info=True,
                )
                return None

        return await self._run(
            operation,
            authenticate=True,
            operation_name="clock_sync_with_native_refresh"
            if sync_clock
            else "native_refresh",
        )

    async def async_set_unit(self, unit: str) -> str:
        """Authenticate, set display unit and verify it by readback."""

        payload = self._unit_payload(unit)

        async def operation(client: BleakClient) -> str:
            if not _has_characteristic(client, UNIT_CHAR_UUID):
                raise UnsupportedMiBLEDeviceError("unit characteristic is unavailable")
            await client.write_gatt_char(UNIT_CHAR_UUID, payload, response=True)
            readback = bytes(await client.read_gatt_char(UNIT_CHAR_UUID))
            return payload_to_unit(readback)

        return await self._run(operation, authenticate=True, operation_name="set_unit")

    async def async_read_environment(self) -> EnvironmentReading:
        """Read one native GATT notification for diagnostics/fallback."""

        async def operation(client: BleakClient) -> EnvironmentReading:
            return await _read_environment_notification(client)

        return await self._run(
            operation, authenticate=True, operation_name="native_refresh"
        )
