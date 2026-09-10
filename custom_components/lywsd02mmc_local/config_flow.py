"""UI configuration for completely local LYWSD02MMC activation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, override

import voluptuous as vol
from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .bluetooth import DeviceNotFoundError, LYWSD02MMCConnectionManager
from .const import (
    CONF_AUTO_SYNC,
    CONF_BINDKEY,
    CONF_DEVICE_ID,
    CONF_FIRMWARE,
    CONF_HARDWARE,
    CONF_METHOD,
    CONF_PRODUCT_ID,
    CONF_SUPPORTS_TIME,
    CONF_SUPPORTS_UNIT,
    CONF_TOKEN,
    DOMAIN,
    MIBEACON_SERVICE_UUID,
    PRODUCTS,
)
from .crypto import AuthenticationTagError
from .discovery import (
    needs_reactivation_warning,
    parse_hex_credential,
    recognize_advertisement,
)
from .mibeacon import (
    InvalidAdvertisementError,
    UnsupportedProductError,
    identify_service_data,
    parse_service_data,
)
from .models import DeviceCredentials, DeviceMetadata
from .provision import (
    DisconnectedError,
    InvalidProtocolResponseError,
    MiBLEActivationError,
    MiBLEAuthenticationError,
    MiBLETimeoutError,
    UnsupportedMiBLEDeviceError,
)

_LOGGER = logging.getLogger(__name__)


def _service_data(info: BluetoothServiceInfoBleak) -> bytes | None:
    return info.service_data.get(MIBEACON_SERVICE_UUID)


def _recognize(info: BluetoothServiceInfoBleak) -> tuple[int | None, bool | None]:
    result = recognize_advertisement(
        name=info.name, address=info.address, service_data=info.service_data
    )
    return result.product_id, result.registered


class LYWSD02MMCLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle local discovery, activation and manual-key recovery."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._product_id: int | None = None
        self._registered: bool | None = None
        self._auto_sync = True
        self._activation_task: (
            asyncio.Task[tuple[DeviceCredentials, DeviceMetadata, str | None]] | None
        ) = None
        self._activation_error: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> LYWSD02MMCOptionsFlow:
        """Create the integration options flow."""

        return LYWSD02MMCOptionsFlow()

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle an FE95/name discovery from HA's native Bluetooth stack."""

        try:
            product_id, registered = _recognize(discovery_info)
        except (InvalidAdvertisementError, UnsupportedProductError):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._set_device(discovery_info, product_id, registered)
        self.context["title_placeholders"] = {
            "name": discovery_info.name or "LYWSD02MMC",
            "address": discovery_info.address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show discovery details before selecting the credential path."""

        assert self._discovery_info is not None
        if user_input is not None:
            return await self.async_step_method()
        self._set_confirm_only()
        product = (
            PRODUCTS.get(self._product_id) if self._product_id is not None else None
        )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery_info.name or "LYWSD02MMC",
                "address": self._discovery_info.address,
                "rssi": str(self._discovery_info.rssi),
                "product": (
                    f"{product.model} / {product.revision} (0x{self._product_id:04X})"
                    if product and self._product_id is not None
                    else "LYWSD02 family (GATT verification required)"
                ),
            },
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow explicit selection from HA's discovery cache."""

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            info = self._discovered[address]
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            product_id, registered = _recognize(info)
            self._set_device(info, product_id, registered)
            return await self.async_step_method()

        await bluetooth.async_request_active_scan(self.hass)
        current = self._async_current_ids(include_ignore=False)
        for info in bluetooth.async_discovered_service_info(self.hass, False):
            try:
                _recognize(info)
            except (InvalidAdvertisementError, UnsupportedProductError):
                continue
            if format_mac(info.address) in current:
                continue
            self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: (
                                f"{info.name or 'LYWSD02MMC'} "
                                f"({address}, {info.rssi} dBm)"
                            )
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose local activation or existing credentials."""

        if user_input is not None:
            self._auto_sync = user_input[CONF_AUTO_SYNC]
            if user_input[CONF_METHOD] == "manual_bindkey":
                return await self.async_step_manual_bindkey()
            # Unknown is handled conservatively: never risk overwriting a binding
            # without showing the destructive-operation warning.
            if needs_reactivation_warning(self._registered):
                return await self.async_step_reactivate_warning()
            return await self.async_step_activate()
        return self.async_show_form(
            step_id="method",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METHOD, default="activate_local"): SelectSelector(
                        SelectSelectorConfig(
                            options=["activate_local", "manual_bindkey"],
                            translation_key="credential_method",
                        )
                    ),
                    vol.Required(CONF_AUTO_SYNC, default=True): bool,
                }
            ),
            description_placeholders={
                "state": (
                    "already activated"
                    if self._registered is True
                    else "not activated"
                    if self._registered is False
                    else "unknown"
                )
            },
        )

    async def async_step_reactivate_warning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require an explicit confirmation before replacing Xiaomi credentials."""

        if user_input is not None:
            return await self.async_step_activate()
        self._set_confirm_only()
        return self.async_show_form(step_id="reactivate_warning")

    async def async_step_activate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run provisioning as a progress task."""

        if self._activation_task is None:
            self._activation_task = self.hass.async_create_task(
                self._async_activate(), "LYWSD02MMC local activation"
            )
            return self.async_show_progress(
                step_id="activate",
                progress_action="provisioning",
                progress_task=self._activation_task,
            )
        if not self._activation_task.done():
            return self.async_show_progress(
                step_id="activate",
                progress_action="provisioning",
                progress_task=self._activation_task,
            )
        return self.async_show_progress_done(next_step_id="activation_finish")

    async def async_step_activation_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Commit credentials only after activation and login both succeeded."""

        assert self._activation_task is not None
        try:
            credentials, metadata, display_unit = self._activation_task.result()
        except Exception as err:  # mapped without logging any key material
            self._activation_error = self._map_error(err)
            _LOGGER.debug("Local MiBLE activation failed: %s", type(err).__name__)
            return await self.async_step_activation_failed()
        return self._create_entry(credentials, metadata, display_unit)

    async def async_step_activation_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a retryable, specific provisioning failure."""

        if user_input is not None:
            self._activation_task = None
            self._activation_error = None
            return await self.async_step_activate()
        return self.async_show_form(
            step_id="activation_failed",
            errors={"base": self._activation_error or "unknown"},
        )

    async def async_step_manual_bindkey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a previously activated clock with user-held credentials."""

        errors: dict[str, str] = {}
        if user_input is not None:
            bindkey_hex = user_input[CONF_BINDKEY].strip().lower()
            token_hex = user_input.get(CONF_TOKEN, "").strip().lower()
            try:
                bindkey = parse_hex_credential(bindkey_hex, 16)
            except ValueError:
                errors[CONF_BINDKEY] = "invalid_bindkey"
            if token_hex:
                try:
                    parse_hex_credential(token_hex, 12)
                except ValueError:
                    errors[CONF_TOKEN] = "invalid_token"
            if not errors:
                assert self._discovery_info is not None
                if data := _service_data(self._discovery_info):
                    try:
                        header = identify_service_data(
                            data, self._discovery_info.address
                        )
                        if header.encrypted and header.includes_objects:
                            parse_service_data(
                                data, self._discovery_info.address, bindkey
                            )
                    except AuthenticationTagError:
                        errors[CONF_BINDKEY] = "invalid_bindkey"
                    except InvalidAdvertisementError:
                        pass
                if not errors:
                    manager = self._manager(
                        token=bytes.fromhex(token_hex) if token_hex else None
                    )
                    try:
                        metadata, display_unit = await manager.async_probe()
                    except Exception as err:
                        errors["base"] = self._map_error(err)
                    else:
                        credentials = DeviceCredentials(
                            token=bytes.fromhex(token_hex) if token_hex else b"",
                            bindkey=bindkey,
                            device_id=b"",
                        )
                        return self._create_entry(
                            credentials,
                            metadata,
                            display_unit,
                            include_empty_token=False,
                        )
        return self.async_show_form(
            step_id="manual_bindkey",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BINDKEY): str,
                    vol.Optional(CONF_TOKEN, default=""): str,
                }
            ),
            errors=errors,
        )

    def _set_device(
        self,
        info: BluetoothServiceInfoBleak,
        product_id: int | None,
        registered: bool | None,
    ) -> None:
        self._discovery_info = info
        self._product_id = product_id
        self._registered = registered

    def _manager(self, token: bytes | None = None) -> LYWSD02MMCConnectionManager:
        assert self._discovery_info is not None
        return LYWSD02MMCConnectionManager(
            self.hass,
            self._discovery_info.address,
            token=token,
            product_id=self._product_id,
        )

    async def _async_activate(
        self,
    ) -> tuple[DeviceCredentials, DeviceMetadata, str | None]:
        return await self._manager().async_activate()

    def _create_entry(
        self,
        credentials: DeviceCredentials,
        metadata: DeviceMetadata,
        display_unit: str | None,
        *,
        include_empty_token: bool = True,
    ) -> ConfigFlowResult:
        assert self._discovery_info is not None
        data: dict[str, Any] = {
            CONF_ADDRESS: self._discovery_info.address,
            CONF_BINDKEY: credentials.bindkey.hex(),
            CONF_PRODUCT_ID: metadata.product_id,
            CONF_DEVICE_ID: credentials.device_id.hex(),
            "model": metadata.model,
            "revision": metadata.revision,
            "manufacturer": metadata.manufacturer,
            CONF_FIRMWARE: metadata.firmware,
            CONF_HARDWARE: metadata.hardware,
            "software": metadata.software,
            CONF_SUPPORTS_TIME: metadata.supports_time,
            CONF_SUPPORTS_UNIT: metadata.supports_unit,
            CONF_AUTO_SYNC: self._auto_sync,
            "display_unit": display_unit,
        }
        if credentials.token or include_empty_token:
            data[CONF_TOKEN] = credentials.token.hex()
        title = f"{metadata.model} {self._discovery_info.address[-5:]}"
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def _map_error(err: Exception) -> str:
        if isinstance(err, DeviceNotFoundError):
            return "device_not_found"
        if isinstance(err, DisconnectedError):
            return "disconnected"
        if isinstance(err, MiBLETimeoutError | TimeoutError):
            return "timeout"
        if isinstance(err, UnsupportedMiBLEDeviceError):
            return "not_supported"
        if isinstance(err, MiBLEActivationError):
            return "activation_refused"
        if isinstance(err, MiBLEAuthenticationError):
            return "authentication_failed"
        if isinstance(err, InvalidProtocolResponseError):
            return "malformed_response"
        if isinstance(err, BleakError):
            return "cannot_connect"
        return "unknown"


class LYWSD02MMCOptionsFlow(OptionsFlowWithReload):
    """Configure automatic clock synchronization."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""

        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONF_AUTO_SYNC,
            self.config_entry.data.get(CONF_AUTO_SYNC, True),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_AUTO_SYNC, default=current): bool}
            ),
        )
