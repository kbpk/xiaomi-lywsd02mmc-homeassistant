# LYWSD02MMC Local for Home Assistant

A custom Home Assistant integration for Xiaomi/Mijia/Miaomiaoce LYWSD02 family
temperature and humidity clocks. Discovery, MiBLE activation, credential
generation, advertisement decryption and clock control stay on the local
Bluetooth path. There is no Xiaomi account, Xiaomi Cloud, Mi Home, token
extractor, MQTT bridge, ESPHome node or external daemon.

The manifest intentionally contains `"requirements": []`. Cryptography uses
the `cryptography` package pinned directly by Home Assistant Core (the inspected
2026.10 development tree pins 48.0.1); BLE connections use `bleak` and
`bleak-retry-connector`, guaranteed by the declared Home Assistant `bluetooth`
dependency.

## Supported devices and entities

Known MiBeacon IDs are `0x045B` (original LYWSD02/t1), `0x16E4`
(LYWSD02MMC/o2 international revision) and `0x2542` (LYWSD02MMC/t8 Telink
revision). The integration creates one device with:

- temperature (°C internally), humidity and battery sensors;
- an optional disabled-by-default diagnostic RSSI sensor;
- **Synchronize clock** when the time characteristic exists;
- **Temperature display unit** when the unit characteristic exists.

Normal measurements are passive encrypted MiBeacon advertisements. The
integration does not poll or retain a GATT connection. It connects only for
activation/login, time synchronization, unit changes or an explicit diagnostic
read, serializes those operations per device, and disconnects in `finally`.

## Installation with HACS

This integration is distributed as a public HACS custom repository. Open the
repository directly in HACS:

[![Open your Home Assistant instance and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kbpk&repository=xiaomi-lywsd02mmc-homeassistant&category=integration)

Alternatively, in HACS open **Integrations → three-dot menu → Custom
repositories**, enter
`https://github.com/kbpk/xiaomi-lywsd02mmc-homeassistant`, select category
**Integration**, and install **Xiaomi LYWSD02MMC Local**. Restart Home Assistant,
then open **Settings → Devices & services → Add integration → LYWSD02MMC
Local**. A Bluetooth discovery card may also appear automatically.

## Manual installation

Copy the directory into your Home Assistant configuration so the final path is:

```text
<HA config>/custom_components/lywsd02mmc_local/
```

Restart Home Assistant and add the integration from **Settings → Devices &
services**.

This repository is developed/tested under WSL. That does not by itself expose a
Windows Bluetooth adapter to a Home Assistant process inside WSL. The running
Home Assistant instance still needs a Bluetooth adapter visible to its host, or
a Home Assistant Bluetooth proxy. Passive packets naturally work through
proxies. Active activation and clock/unit writes require a proxy/adapter that
supports connectable GATT routing; no `hci0` is hard-coded.

### Physical-device development from Windows / WSL

For development, the repository includes a read-only GATT diagnostic that uses
the Windows Bluetooth stack through Bleak's WinRT backend. Run it from Windows
PowerShell while keeping the source tree in WSL (replace `Ubuntu` if your WSL
distribution has another name):

```powershell
$repo = "\\wsl.localhost\Ubuntu\home\kbpk\xiaomi\lywsd002mmc"
uv run --no-project --with-requirements "$repo\requirements-lab.txt" `
  "$repo\tools\lywsd02mmc_diag.py" --read --duration 30 `
  --output "$repo\captures\lywsd02mmc-gatt.jsonl"
```

This enumerates GATT, reads only known non-secret characteristics and waits for
one native temperature/humidity notification. It does not send activation or
login commands. The resulting trace is ignored by Git.

A separate `tools/lywsd02mmc_activate.py` exercises the same provisioning state
machine as the HA integration. It is intentionally gated by
`--i-understand-rebinding`, because running it can replace an existing Xiaomi
binding. Use it only after reviewing the read-only trace and making the device
ready for activation:

```powershell
uv run --no-project --with-requirements "$repo\requirements-lab.txt" `
  "$repo\tools\lywsd02mmc_activate.py" --i-understand-rebinding `
  --output "$repo\private\lywsd02mmc-secrets.json" `
  --trace "$repo\captures\lywsd02mmc-activation.jsonl"
```

The credential file is private and ignored by Git. Windows files inherit the
directory ACL instead of the Unix `0600` mode.

## First local activation

1. Insert good CR2032 batteries and place the clock close to an HA Bluetooth
   adapter or connectable proxy. Close Mi Home and BLE scanner apps that might
   hold its only GATT connection.
2. Select the discovered clock. The UI shows local name, address, RSSI and the
   recognized PID/revision when present.
3. Choose **Activate locally / generate credentials**. Optionally leave the
   initial clock synchronization enabled.
4. Home Assistant generates an ephemeral P-256 key and a new local Mi token and
   bindkey, provisions them over FE95, then performs mutual MiBLE login. Only a
   confirmed login creates the config entry. The private ECDH key is discarded.

An unactivated unit is normally connectable after battery insertion. To return
an LYWSD02MMC to its documented factory state, remove the rear cover and, while
the clock is powered, bridge the labelled **Reset** and **Gnd** pads near either
battery with a conductive metal object for more than seven seconds. The clock
restarts. This physical reset instruction comes from the LYWSD02MMC user manual;
pad placement can differ, so follow the labels and do not short a battery.

If an advertisement reports an existing binding, the flow displays a separate
destructive reactivation warning. Reactivation replaces Xiaomi credentials and
can break an existing Mi Home binding. Manual bindkey entry remains available;
the optional 12-byte token enables authenticated active commands on firmware
that requires it.

## Architecture and security

- `crypto.py`: P-256, HKDF-SHA256, HMAC and AES-CCM pure functions.
- `provision.py`: explicit, testable registration/login state machine.
- `mibeacon.py`: strict header recognition, authenticated decryption, object
  parsing and duplicate suppression.
- `bluetooth.py`: current HA discovery/connection helpers and short-lived GATT.
- `coordinator.py`: passive callback and HA learned/fallback availability.
- entity modules: deliberately thin wrappers around accumulated state.

Secrets are stored in the config entry because passive decryption and later
login require them. INFO/WARNING/ERROR logs never print them. Diagnostics redact
token, bindkey, DID and address. DEBUG output includes safe GATT UUID/property
fingerprints, MiBeacon flags/PID/counter/object IDs and state names, but not key
material or decrypted secret fields. See [the protocol notes](docs/protocol.md)
for the complete byte-level flow.

## Clock behavior

Clock writes use the Home Assistant configured timezone at the moment the button
is pressed. Whole-hour offset is stored in the signed fifth byte; half/quarter
hour remainder is folded into the epoch as required by known clients. The
device stores only an offset, not future DST rules. Press **Synchronize clock**
after a DST change or create a modest automation (for example twice yearly).
The integration deliberately does not synchronize every few minutes.

## Troubleshooting and debug logging

If discovery works but activation cannot connect, verify that the Bluetooth
adapter/proxy is connectable, move the clock closer, close other GATT clients,
and reinsert batteries. A passive-only proxy can forward telemetry but cannot
provision or write settings. A timeout names the protocol phase in DEBUG logs;
an authentication failure does not save a half-created entry.

Enable secret-safe debug logging:

```yaml
logger:
  logs:
    custom_components.lywsd02mmc_local: debug
```

Useful messages include the service/characteristic list and properties,
firmware/hardware strings, MiBLE state transitions, PID, frame counter and
object IDs. Never post the raw Home Assistant config entry; it contains the
bindkey and possibly token even though diagnostics redact them.

## Tests and current validation boundary

Run under WSL or Linux:

```bash
python3 -m pytest -q
```

Tests cover independent fixed crypto vectors, P-256 ECDH, setup/login slicing,
AES-CCM tag rejection, complete fresh and reactivation state traces, failure,
malformed/disconnect/timeout paths, real sanitized encrypted advertisements for
`0x16E4` and `0x2542`, synthetic authenticated multi-object/PID/counter cases,
clock offsets/DST, unit values and the zero-requirement integration contract.

The protocol is implemented from working references, but this initial version
still needs real-device validation on the owner's exact LYWSD02MMC: timing of
the full activation exchange, PID/local-name reported during factory state,
whether time/unit writes require a fresh login, and capability behavior on each
firmware revision. DEBUG instrumentation exists specifically to capture those
differences without leaking credentials.

## License

Released under the [MIT License](LICENSE).
