"""Passive MiBeacon coordinator."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import FALLBACK_AVAILABILITY_SECONDS, MIBEACON_SERVICE_UUID
from .crypto import AuthenticationTagError
from .mibeacon import (
    DuplicateFrameError,
    InvalidAdvertisementError,
    MiBeaconParser,
    UnsupportedProductError,
)
from .models import SensorState
from .time import EnvironmentReading

_LOGGER = logging.getLogger(__name__)


class LYWSD02MMCCoordinator(DataUpdateCoordinator[SensorState]):
    """Accumulate independently advertised values for one physical clock."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        bindkey: bytes,
        display_unit: str | None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"LYWSD02MMC {address}",
        )
        self.address = address
        self.parser = MiBeaconParser(address, bindkey)
        self.data = SensorState()
        self.display_unit = display_unit
        self.last_source: str | None = None
        self.last_connectable: bool | None = None

    def async_start(self) -> None:
        """Subscribe to passive advertisements and HA availability tracking."""

        bluetooth.async_set_fallback_availability_interval(
            self.hass, self.address, FALLBACK_AVAILABILITY_SECONDS
        )
        assert self.config_entry is not None
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._async_advertisement,
                BluetoothCallbackMatcher(address=self.address, connectable=False),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        self.config_entry.async_on_unload(
            bluetooth.async_track_unavailable(
                self.hass, self._async_unavailable, self.address, False
            )
        )

    @callback
    def _async_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        data = service_info.service_data.get(MIBEACON_SERVICE_UUID)
        if not data:
            return
        try:
            reading = self.parser.parse(data)
        except DuplicateFrameError:
            return
        except AuthenticationTagError:
            _LOGGER.debug(
                "Rejected encrypted MiBeacon frame with invalid authentication tag"
            )
            return
        except (InvalidAdvertisementError, UnsupportedProductError) as err:
            _LOGGER.debug("Ignored MiBeacon frame: %s", err)
            return

        state = replace(
            self.data,
            temperature=(
                reading.temperature
                if reading.temperature is not None
                else self.data.temperature
            ),
            humidity=(
                reading.humidity if reading.humidity is not None else self.data.humidity
            ),
            battery=(
                reading.battery if reading.battery is not None else self.data.battery
            ),
            rssi=service_info.rssi,
            frame_counter=reading.header.frame_counter,
            object_ids=reading.object_ids,
            last_seen=datetime.now(UTC),
            available=True,
        )
        self.last_source = service_info.source
        self.last_connectable = service_info.connectable
        _LOGGER.debug(
            "Valid encrypted MiBeacon: product=0x%04x counter=%d objects=%s",
            reading.header.product_id,
            reading.header.frame_counter,
            ",".join(f"0x{x:04x}" for x in reading.object_ids) or "none",
        )
        self.async_set_updated_data(state)

    @callback
    def async_set_native_environment(self, reading: EnvironmentReading) -> None:
        """Apply one short-lived native GATT reading as a fallback snapshot."""

        self.async_set_updated_data(
            replace(
                self.data,
                temperature=reading.temperature,
                humidity=reading.humidity,
                battery_voltage=(
                    reading.battery_voltage
                    if reading.battery_voltage is not None
                    else self.data.battery_voltage
                ),
                last_native_update=datetime.now(UTC),
                available=True,
            )
        )

    @callback
    def _async_unavailable(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        self.async_set_updated_data(replace(self.data, available=False))
