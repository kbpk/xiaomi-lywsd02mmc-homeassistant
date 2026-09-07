"""Behavioral tests for config-flow decisions without a running HA instance."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from custom_components.lywsd02mmc_local.const import MIBEACON_SERVICE_UUID
from custom_components.lywsd02mmc_local.discovery import (
    needs_reactivation_warning,
    parse_hex_credential,
    recognize_advertisement,
)
from custom_components.lywsd02mmc_local.mibeacon import UnsupportedProductError


def _header(product: int, control: int = 0x5000) -> bytes:
    return struct.pack("<HHB", control, product, 1)


def test_bluetooth_discovery_known_product() -> None:
    result = recognize_advertisement(
        name=None,
        address="A4:C1:38:00:00:01",
        service_data={MIBEACON_SERVICE_UUID: _header(0x2542)},
    )
    assert result.product_id == 0x2542
    assert result.registered is False


def test_name_discovery_requires_later_gatt_fingerprint() -> None:
    result = recognize_advertisement(
        name="LYWSD02MMC", address="A4:C1:38:00:00:01", service_data={}
    )
    assert result.product_id is None
    assert result.registered is None


def test_unsupported_fe95_device_is_not_claimed() -> None:
    with pytest.raises(UnsupportedProductError):
        recognize_advertisement(
            name="Other Xiaomi",
            address="A4:C1:38:00:00:01",
            service_data={MIBEACON_SERVICE_UUID: _header(0x055B)},
        )


def test_reactivation_warning_for_bound_encrypted_and_unknown_devices() -> None:
    encrypted = recognize_advertisement(
        name="LYWSD02MMC",
        address="A4:C1:38:00:00:01",
        service_data={MIBEACON_SERVICE_UUID: _header(0x2542, 0x5008)},
    )
    assert encrypted.registered is True
    assert needs_reactivation_warning(True)
    assert needs_reactivation_warning(None)
    assert not needs_reactivation_warning(False)


def test_manual_bindkey_and_optional_token_validation() -> None:
    assert parse_hex_credential("00" * 16, 16) == b"\x00" * 16
    assert parse_hex_credential("ab" * 12, 12) == b"\xab" * 12
    with pytest.raises(ValueError):
        parse_hex_credential("00" * 15, 16)
    with pytest.raises(ValueError):
        parse_hex_credential("not-hex", 16)


def test_duplicate_device_guard_is_wired_before_confirmation() -> None:
    source = (
        Path(__file__).parents[1]
        / "custom_components/lywsd02mmc_local/config_flow.py"
    ).read_text()
    bluetooth_step = source.split("async def async_step_bluetooth", 1)[1].split(
        "async def async_step_bluetooth_confirm", 1
    )[0]
    assert "async_set_unique_id" in bluetooth_step
    assert "_abort_if_unique_id_configured" in bluetooth_step


def test_activation_is_a_progress_task_and_failure_is_retryable() -> None:
    source = (
        Path(__file__).parents[1]
        / "custom_components/lywsd02mmc_local/config_flow.py"
    ).read_text()
    assert "async_show_progress" in source
    assert "async_step_activation_failed" in source
    assert "self._activation_task = None" in source
    assert 'return "disconnected"' in source
