"""Pure discovery decisions shared with the Home Assistant config flow."""

from __future__ import annotations

from dataclasses import dataclass

from .const import MIBEACON_SERVICE_UUID, SUPPORTED_LOCAL_NAME_PREFIXES
from .mibeacon import (
    InvalidAdvertisementError,
    UnsupportedProductError,
    identify_service_data,
)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    product_id: int | None
    registered: bool | None


def recognize_advertisement(
    *, name: str | None, address: str, service_data: dict[str, bytes]
) -> DiscoveryResult:
    """Recognize only a known PID or an LYWSD02 name pending GATT proof."""

    if data := service_data.get(MIBEACON_SERVICE_UUID):
        try:
            header = identify_service_data(data, address)
        except (UnsupportedProductError, InvalidAdvertisementError):
            if not (name or "").upper().startswith(SUPPORTED_LOCAL_NAME_PREFIXES):
                raise
        else:
            # Encryption proves that key material exists even on firmware that
            # does not set the nominal registered bit.
            return DiscoveryResult(
                header.product_id, header.registered or header.encrypted
            )
    if (name or "").upper().startswith(SUPPORTED_LOCAL_NAME_PREFIXES):
        return DiscoveryResult(None, None)
    raise UnsupportedProductError("advertisement is not an LYWSD02 family device")


def needs_reactivation_warning(registered: bool | None) -> bool:
    """Warn unless the advertisement explicitly proves an unbound state."""

    return registered is not False


def parse_hex_credential(value: str, expected_length: int) -> bytes:
    """Parse one exact-length credential without accepting malformed data."""

    try:
        parsed = bytes.fromhex(value.strip())
    except ValueError as err:
        raise ValueError("credential is not hexadecimal") from err
    if len(parsed) != expected_length:
        raise ValueError(f"credential must contain {expected_length} bytes")
    return parsed
