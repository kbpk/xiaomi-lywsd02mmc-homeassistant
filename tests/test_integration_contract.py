from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "lywsd02mmc_local"


def test_manifest_has_no_requirements_and_native_bluetooth_dependency() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert manifest["requirements"] == []
    assert manifest["dependencies"] == ["bluetooth"]
    assert any(
        matcher.get("service_data_uuid") == "0000fe95-0000-1000-8000-00805f9b34fb"
        for matcher in manifest["bluetooth"]
    )


def test_required_config_flow_paths_are_present() -> None:
    source = (INTEGRATION / "config_flow.py").read_text()
    for step in (
        "async_step_bluetooth",
        "async_step_user",
        "async_step_method",
        "async_step_reactivate_warning",
        "async_step_activate",
        "async_step_manual_bindkey",
        "async_show_progress",
        "async_get_options_flow",
        "OptionsFlowWithReload",
    ):
        assert step in source


def test_no_forbidden_runtime_imports() -> None:
    source = "\n".join(path.read_text() for path in INTEGRATION.glob("*.py"))
    for forbidden in (
        "xiaomi_ble",
        "ble_monitor",
        "esphome",
        "miio",
        "Cryptodome",
        "paho",
    ):
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source


def test_setup_constructor_wiring() -> None:
    """The config entry owns coordinator callbacks, not BLE connections."""

    tree = ast.parse((INTEGRATION / "__init__.py").read_text())
    calls = {
        node.func.id: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    connection = calls["LYWSD02MMCConnectionManager"]
    coordinator = calls["LYWSD02MMCCoordinator"]
    assert [arg.id for arg in connection.args if isinstance(arg, ast.Name)] == [
        "hass",
        "address",
    ]
    assert [arg.id for arg in coordinator.args if isinstance(arg, ast.Name)][:3] == [
        "hass",
        "entry",
        "address",
    ]


def test_device_info_includes_firmware_and_hardware_versions() -> None:
    source = (INTEGRATION / "entity.py").read_text()
    assert "hw_version=metadata.hardware or metadata.revision" in source
    assert "sw_version=metadata.firmware or metadata.software" in source


def test_feature_complete_diagnostics_and_clock_scheduler_are_wired() -> None:
    init_source = (INTEGRATION / "__init__.py").read_text()
    clock_source = (INTEGRATION / "clock.py").read_text()
    sensor_source = (INTEGRATION / "sensor.py").read_text()
    diagnostics_source = (INTEGRATION / "diagnostics.py").read_text()
    assert "AutomaticClockSynchronizer" in init_source
    assert "async_track_point_in_utc_time" in clock_source
    assert "EVENT_CORE_CONFIG_UPDATE" in clock_source
    assert 'key="battery_voltage"' in sensor_source
    assert "entity_registry_enabled_default=False" in sensor_source
    assert '"active_gatt"' in diagnostics_source
    assert '"automatic_clock_sync"' in diagnostics_source


def test_passive_callback_explicitly_accepts_nonconnectable_sources() -> None:
    source = (INTEGRATION / "coordinator.py").read_text()
    assert "BluetoothCallbackMatcher(address=self.address, connectable=False)" in source
