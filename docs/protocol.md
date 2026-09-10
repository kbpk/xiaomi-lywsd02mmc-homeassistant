# LYWSD02MMC local protocol notes

These notes document what this integration implements. They were reconstructed
from the working `Temp_universal_mi_activate.html` and `TelinkFlasher.html`
flows, then cross-checked against the current Xiaomi BLE, BLE Monitor and
ESPHome parsers. No Xiaomi server participates.

## GATT layout

- MiBeacon/MiBLE service: `0000fe95-0000-1000-8000-00805f9b34fb`
- MiBLE control: `00000010-0000-1000-8000-00805f9b34fb`
- MiBLE transfer: `00000019-0000-1000-8000-00805f9b34fb`
- Clock/environment service: `ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6`
- Time: `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6`
- Display unit: `ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6`
- Direct temperature/humidity: `ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6`

Both MiBLE characteristics must notify. Protocol transfer fragments start with
`fragment_number, 0x00`; the reference implementation carries at most 18 data
bytes in each fragment.

On the physically tested PID `0x2542`, both characteristics advertise
`write-without-response` (not `write`). The transport therefore selects the
write mode from each discovered characteristic instead of forcing ATT write
requests. This matters on strict Bleak backends such as Windows WinRT.

## Registration state machine

The integration enables notifications before sending anything and bounds each
wait. The wire sequence is:

1. Host writes `a2000000` to characteristic `0x0010`.
2. Device reports unbound (`000000000100`) or bound (`000000000200`) on
   `0x0019`; host acknowledges with `00000101`.
3. For a bound device, the device returns its existing DID in two fragments.
   Four leading protocol bytes are removed and the DID is preserved during
   reactivation. A fresh device instead returns `010001000000`.
4. Host acknowledges, writes `15000000` to `0x0010`, then
   `000000030400` to `0x0019`.
5. On `00000101`, the host sends the 64-byte X/Y part of its uncompressed
   P-256 public key in four fragments. The private key is ephemeral.
6. PID `0x2542` firmware `2.0.1_0021` sends an additional `00000100` ACK after
   the fourth host-key fragment. The handler accepts this status only while
   waiting for the device key. The device then returns its own X/Y coordinates
   in four fragments. The host
   prepends SEC1 byte `04`, validates the point and performs P-256 ECDH.
7. RFC 5869 HKDF-SHA256 expands the 32-byte ECDH secret to 64 bytes with no
   explicit salt and info `mible-setup-info`:

   | Bytes | Meaning |
   | --- | --- |
   | `0..11` | 12-byte Mi token |
   | `12..27` | 16-byte MiBeacon bindkey |
   | `28..43` | 16-byte DID encryption key (`mi_bind_A`) |
   | `44..63` | Reserved setup material |

8. The DID is AES-CCM encrypted with a four-byte tag, nonce
   `101112131415161718191a1b`, and AAD ASCII `devID`. After the device asks
   with `00000101`, ciphertext plus tag is sent in two fragments.
9. Device acknowledges; host writes `13000000` to `0x0010`. Only
   `11000000` is registration success; `12000000` is failure.
10. Registration success immediately starts the login below. Credentials are
    not exposed to the config flow until login returns `21000000`.

The fresh DID has the exact 20-byte shape used by the working reference:
`00 || "blt.3.129v" || six random alphanumerics || "ATC"`. It is not a secret.

## Login / authentication

1. Host creates 16 random bytes, writes `24000000` to `0x0010`, then
   `0000000b0100` to `0x0019`.
2. On `00000101`, host sends `0100 || host_random`.
3. Confirmed t8 firmware may first send transfer ACK `00000100`. Device then
   sends `0000000d0100`; host acknowledges with `00000101`.
4. Device sends `0100 || device_random`.
5. HKDF-SHA256 expands the 12-byte token to 64 bytes using salt
   `host_random || device_random` and info `mible-login-info`.
6. Expected device proof is HMAC-SHA256 keyed by derived bytes `0..15` over
   `device_random || host_random`. Host proof is HMAC-SHA256 keyed by derived
   bytes `16..31` over `host_random || device_random`.
7. The device proof is requested, reassembled from two fragments and compared
   in constant time. A mismatch aborts the operation.
8. Host acknowledges and writes `0000000a0200`, then sends its proof in two
   fragments when requested.
9. `21000000` on `0x0010` is success and `23000000` is failure.

The state machine accepts the t8's extra `00000100` transfer acknowledgements
only in login phases immediately following host writes; it does not treat
arbitrary unexpected packets as harmless.

## MiBeacon v4/v5 authenticated decryption

Service data begins with little-endian frame control, little-endian product ID,
and an eight-bit frame counter. Optional embedded MAC and capability fields are
skipped according to frame-control bits. For encrypted v4/v5 frames:

- ciphertext is the object area up to the final seven bytes;
- the last seven bytes are a three-byte extended counter and four-byte CCM tag;
- nonce is `MAC_in_advertisement_order || product_id_and_frame_counter ||
  extended_counter` (12 bytes);
- AAD is `11`;
- AES-CCM uses the 16-byte bindkey and a four-byte tag.

An invalid tag is rejected before any object is parsed. Exact recent frames are
suppressed; counter rollover remains valid because suppression uses the full
authenticated frame. Object-bearing plaintext frames are not allowed to update
entities after setup. Supported classic objects are `0x1004` temperature,
`0x1006` humidity, `0x100A` battery, and `0x100D` combined temperature and
humidity. Newer LYWSD02MMC revisions use float/single-byte objects
`0x4801..03` or `0x4C01..03`; both observed namespaces are accepted. Float
humidity variants `0x4808` and `0x4C08` are also decoded.

Product mapping used by this integration:

| MiBeacon PID | Ecosystem label | Xiaomi model family |
| --- | --- | --- |
| `0x045B` (1115) | `LYWSD02`, original clock | `miaomiaoce.sensor_ht.t1` |
| `0x16E4` (5860) | `LYWSD02MMC`, international clock | `miaomiaoce.sensor_ht.o2` |
| `0x2542` (9538) | `LYWSD02MMC`, newer Telink clock | `miaomiaoce.sensor_ht.t8` |

The commercial casing/model naming overlaps; discovery therefore combines PID,
local name, address validation and the active GATT fingerprint instead of
relying on a name alone.

## Clock and unit

The common writable prefix is five bytes: little-endian unsigned Unix epoch
followed by a signed whole-hour UTC offset byte. For zones with a 30- or
45-minute component, known clients fold the remainder into the epoch.

Physical testing corrected an initially misleading readback result on PID
`0x2542`: the characteristic echoes arbitrary writes, but the display interprets
the fifth byte as hours. Writing `08` in Warsaw produced UTC+8 on screen; writing
`02` produced the correct UTC+2 local time. Echo verification alone therefore
does not prove the semantic meaning of a setting.

Firmware `2.0.1_0021` on PID `0x2542` returned seven bytes from the time
characteristic: the five-byte prefix plus two zero bytes. Writes remain the
five-byte prefix; verification accepts and ignores the observed trailing
revision fields. The integration computes the current offset from Home
Assistant's configured IANA timezone at write time. There is no timezone rule
database in the clock. The integration therefore calculates the next IANA
timezone offset transition, schedules one short-lived connection just after
that transition, and schedules the following one. It also synchronizes once at
Home Assistant startup when the option is enabled; it does not poll over BLE.

The unit characteristic is one byte. Physical PID `0x2542` returned `00` for
Celsius and uses `01` for Fahrenheit. Original PID `0x045B` references use
`FF` for Celsius and `01` for Fahrenheit, so writes are product-aware and reads
accept both Celsius encodings.

The native measurement prefix is signed little-endian temperature in
hundredths of a degree Celsius followed by integer RH. Original devices send
exactly three bytes. Physical PID `0x2542` sent five bytes; its final uint16 was
battery voltage in millivolts (`5b0b34f40a` = 29.07 °C, 52%, 2804 mV).

## Sources and validation status

- Activation/login: [atc1441 local activation reference](https://github.com/atc1441/atc1441.github.io/blob/main/Temp_universal_mi_activate.html)
  and [TelinkFlasher](https://github.com/atc1441/atc1441.github.io/blob/main/TelinkFlasher.html).
- MiBeacon framing and real vectors: [Bluetooth-Devices/xiaomi-ble](https://github.com/Bluetooth-Devices/xiaomi-ble)
  and [BLE Monitor parser](https://github.com/custom-components/ble_monitor/blob/master/custom_components/ble_monitor/ble_parser/xiaomi.py).
- Independent embedded cross-check: [ESPHome Xiaomi BLE parser](https://github.com/esphome/esphome/tree/dev/esphome/components/xiaomi_ble).
- Clock/unit byte format: [LYWSD02 clock sync reference](https://gist.github.com/luiseduardobrito/d6733a884b0e44996d1c8bec52242ace).

Crypto, parser and state-transition vectors are automated. Windows/WinRT tests
on the owner's PID `0x2542` have confirmed local name `LYWSD02MMC`, firmware
`2.0.1_0021`, hardware `F4_M1`, manufacturer `miaomiaoce.com`, the full GATT
fingerprint, fresh activation, reactivation with DID reassembly, immediate
post-activation login, login over a later fresh connection, authenticated
MiBeacon temperature/humidity frames, five-byte live data, time synchronization
with seven-byte readback, the signed whole-hour Warsaw summer offset `02`, Celsius
write/readback and prompt disconnects. A real passive battery object was not
observed during the bounded capture window; its parser remains covered by
authenticated test vectors and will update the entity when broadcast.
