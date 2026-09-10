"""Automatic, transition-aware synchronization for the physical clock."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time

from .bluetooth import LYWSD02MMCConnectionManager
from .coordinator import LYWSD02MMCCoordinator
from .time import next_offset_transition

_LOGGER = logging.getLogger(__name__)

TRANSITION_SETTLE_DELAY = timedelta(seconds=30)


@dataclass(slots=True)
class ClockSyncState:
    """Secret-free automatic clock synchronization status."""

    enabled: bool
    last_reason: str | None = None
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    last_synced_offset_minutes: int | None = None
    next_transition: datetime | None = None


class AutomaticClockSynchronizer:
    """Sync once at HA startup and exactly after UTC-offset transitions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        connection: LYWSD02MMCConnectionManager,
        coordinator: LYWSD02MMCCoordinator,
        *,
        enabled: bool,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.connection = connection
        self.coordinator = coordinator
        self.state = ClockSyncState(enabled=enabled)
        self._operation_lock = asyncio.Lock()
        self._background_task: asyncio.Task[None] | None = None
        self._unsub_start: CALLBACK_TYPE | None = None
        self._unsub_transition: CALLBACK_TYPE | None = None
        self._unsub_config: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> None:
        """Attach lifecycle and timezone listeners."""

        self.entry.async_on_unload(self.async_stop)
        self._unsub_config = self.hass.bus.async_listen(
            EVENT_CORE_CONFIG_UPDATE, self._handle_core_config_update
        )
        self._schedule_next_transition()
        if not self.state.enabled:
            return
        if self.hass.state is CoreState.running:
            self._queue_sync("startup")
            return
        self._unsub_start = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, self._handle_started
        )

    @callback
    def async_stop(self) -> None:
        """Detach all listeners; config-entry tasks are canceled by HA."""

        for attribute in (
            "_unsub_start",
            "_unsub_transition",
            "_unsub_config",
        ):
            if unsubscribe := getattr(self, attribute):
                unsubscribe()
                setattr(self, attribute, None)

    @callback
    def _handle_started(self, _event: Any) -> None:
        self._unsub_start = None
        self._queue_sync("startup")

    @callback
    def _handle_transition(self, _now: datetime) -> None:
        self._unsub_transition = None
        self._schedule_next_transition()
        self._queue_sync("timezone_transition")

    @callback
    def _handle_core_config_update(self, _event: Any) -> None:
        previous_offset = self._current_offset_minutes()
        self._schedule_next_transition()
        if (
            self.state.enabled
            and self.state.last_synced_offset_minutes is not None
            and previous_offset != self.state.last_synced_offset_minutes
        ):
            self._queue_sync("home_assistant_timezone_change")

    def _timezone(self) -> ZoneInfo:
        return ZoneInfo(self.hass.config.time_zone)

    def _current_offset_minutes(self) -> int:
        offset = datetime.now(self._timezone()).utcoffset()
        assert offset is not None
        return int(offset.total_seconds() / 60)

    @callback
    def _schedule_next_transition(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None
        self.state.next_transition = None
        if not self.state.enabled:
            return
        transition = next_offset_transition(datetime.now(UTC), self._timezone())
        if transition is None:
            return
        self.state.next_transition = transition
        self._unsub_transition = async_track_point_in_utc_time(
            self.hass,
            self._handle_transition,
            transition + TRANSITION_SETTLE_DELAY,
        )
        _LOGGER.debug("Next automatic clock timezone sync at %s", transition)

    @callback
    def _queue_sync(self, reason: str) -> None:
        if self._background_task is not None and not self._background_task.done():
            return
        task = self.entry.async_create_background_task(
            self.hass,
            self.async_synchronize(reason=reason, raise_errors=False),
            f"LYWSD02MMC automatic clock sync ({reason})",
        )
        self._background_task = task
        task.add_done_callback(self._clear_background_task)

    @callback
    def _clear_background_task(self, task: asyncio.Task[None]) -> None:
        if self._background_task is task:
            self._background_task = None

    async def async_synchronize(
        self, *, reason: str, raise_errors: bool = True
    ) -> None:
        """Synchronize and opportunistically capture native battery voltage."""

        async with self._operation_lock:
            self.state.last_reason = reason
            self.state.last_attempt = datetime.now(UTC)
            self.state.last_error = None
            timezone = self._timezone()
            now = datetime.now(timezone)
            try:
                if self.connection.product_id == 0x2542:
                    reading = await self.connection.async_refresh(
                        now, sync_clock=True
                    )
                else:
                    await self.connection.async_sync_clock(now)
                    reading = None
            except Exception as err:
                self.state.last_error = type(err).__name__
                _LOGGER.debug(
                    "Clock synchronization failed (%s): %s",
                    reason,
                    type(err).__name__,
                )
                if raise_errors:
                    raise
                return
            if reading is not None:
                self.coordinator.async_set_native_environment(reading)
            offset = now.utcoffset()
            assert offset is not None
            self.state.last_synced_offset_minutes = int(offset.total_seconds() / 60)
            self.state.last_success = datetime.now(UTC)
            _LOGGER.debug("Clock synchronization confirmed (%s)", reason)

    async def async_refresh_environment(self) -> None:
        """Capture a native snapshot when its diagnostic entity is enabled."""

        async with self._operation_lock:
            reading = await self.connection.async_read_environment()
            self.coordinator.async_set_native_environment(reading)
