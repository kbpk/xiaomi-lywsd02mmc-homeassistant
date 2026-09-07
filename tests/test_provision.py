from __future__ import annotations

import asyncio

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from custom_components.lywsd02mmc_local.const import MIBLE_CHAR_10, MIBLE_CHAR_19
from custom_components.lywsd02mmc_local.crypto import (
    derive_login_material,
    derive_setup_material,
    p256_public_key_bytes,
    p256_shared_secret,
)
from custom_components.lywsd02mmc_local.provision import (
    DisconnectedError,
    InvalidProtocolResponseError,
    MiBLEActivationError,
    MiBLEAuthenticationError,
    MiBLEStateMachine,
    MiBLETimeoutError,
    Phase,
    run_state_machine,
)


def _feed_device_public_key(
    machine: MiBLEStateMachine, device_private: ec.EllipticCurvePrivateKey
) -> None:
    public = p256_public_key_bytes(device_private)[1:]
    for index, start in enumerate(range(0, 64, 18), 1):
        writes = machine.feed(
            MIBLE_CHAR_19, bytes((index, 0)) + public[start : start + 18]
        )
    assert [write.data for write in writes] == [
        bytes.fromhex("00000100"),
        bytes.fromhex("000000000200"),
    ]


def _advance_fresh_activation(
    machine: MiBLEStateMachine, device_private: ec.EllipticCurvePrivateKey
) -> bytes:
    assert machine.start_activation()[0].data == bytes.fromhex("a2000000")
    assert machine.feed(MIBLE_CHAR_19, bytes.fromhex("000000000100"))[0].data == (
        bytes.fromhex("00000101")
    )
    begin = machine.feed(MIBLE_CHAR_19, bytes.fromhex("010001000000"))
    assert [item.data for item in begin] == [
        bytes.fromhex("00000100"),
        bytes.fromhex("15000000"),
        bytes.fromhex("000000030400"),
    ]
    public_writes = machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    assert len(public_writes) == 4
    assert [len(item.data) for item in public_writes] == [20, 20, 20, 12]
    _feed_device_public_key(machine, device_private)
    assert len(machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))) == 2
    assert machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000100"))[0].data == (
        bytes.fromhex("13000000")
    )
    setup = derive_setup_material(
        p256_shared_secret(
            machine.private_key, p256_public_key_bytes(device_private)
        )
    )
    return setup.token


def _complete_login(machine: MiBLEStateMachine, token: bytes) -> None:
    start = machine.feed(MIBLE_CHAR_10, bytes.fromhex("11000000"))
    assert [write.data for write in start] == [
        bytes.fromhex("24000000"),
        bytes.fromhex("0000000b0100"),
    ]
    random_write = machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    assert random_write[0].data == b"\x01\x00" + machine.client_random
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("0000000d0100"))
    device_random = bytes(range(16, 32))
    machine.feed(MIBLE_CHAR_19, b"\x01\x00" + device_random)
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("0000000c0200"))
    material = derive_login_material(token, machine.client_random, device_random)
    machine.feed(MIBLE_CHAR_19, b"\x01\x00" + material.expected_device_proof[:18])
    proof_ack = machine.feed(
        MIBLE_CHAR_19, b"\x02\x00" + material.expected_device_proof[18:]
    )
    assert [write.data for write in proof_ack] == [
        bytes.fromhex("00000100"),
        bytes.fromhex("0000000a0200"),
    ]
    client_proof = machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    assert b"".join(write.data[2:] for write in client_proof) == material.client_proof
    machine.feed(MIBLE_CHAR_10, bytes.fromhex("21000000"))


def test_fresh_activation_and_successful_subsequent_login() -> None:
    private = ec.derive_private_key(1, ec.SECP256R1())
    device_private = ec.derive_private_key(2, ec.SECP256R1())
    machine = MiBLEStateMachine(
        private_key=private,
        client_random=bytes(range(16)),
        device_id=b"\x00blt.3.129vABC123ATC",
    )
    token = _advance_fresh_activation(machine, device_private)
    _complete_login(machine, token)
    assert machine.phase is Phase.COMPLETE
    assert machine.credentials.token == token
    assert len(machine.credentials.bindkey) == 16


def test_reactivation_preserves_existing_device_id() -> None:
    machine = MiBLEStateMachine(
        private_key=ec.derive_private_key(1, ec.SECP256R1()),
        client_random=b"r" * 16,
    )
    machine.start_activation()
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("000000000200"))
    existing_did = b"\x00blt.3.129vOLD123ATC"
    response = b"HEAD" + existing_did
    machine.feed(MIBLE_CHAR_19, b"\x01\x00" + response[:18])
    writes = machine.feed(MIBLE_CHAR_19, b"\x02\x00" + response[18:])
    assert machine.was_activated
    assert machine.device_id == existing_did
    assert machine.phase is Phase.WAIT_PUBLIC_KEY_REQUEST
    assert len(writes) == 3

    device_private = ec.derive_private_key(2, ec.SECP256R1())
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    _feed_device_public_key(machine, device_private)
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000100"))
    token = derive_setup_material(
        p256_shared_secret(
            machine.private_key, p256_public_key_bytes(device_private)
        )
    ).token
    _complete_login(machine, token)
    assert machine.complete
    assert machine.credentials.device_id == existing_did


def test_activation_failure_and_malformed_fragment() -> None:
    machine = MiBLEStateMachine()
    machine.start_activation()
    with pytest.raises(MiBLEActivationError):
        machine.feed(MIBLE_CHAR_10, bytes.fromhex("12000000"))

    machine = MiBLEStateMachine()
    machine.start_activation()
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("000000000100"))
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("010001000000"))
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    with pytest.raises(InvalidProtocolResponseError):
        machine.feed(MIBLE_CHAR_19, b"\x05\x00bad")


def test_bad_device_login_proof() -> None:
    machine = MiBLEStateMachine(token=b"t" * 12, client_random=b"c" * 16)
    machine.start_login()
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("00000101"))
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("0000000d0100"))
    machine.feed(MIBLE_CHAR_19, b"\x01\x00" + b"d" * 16)
    machine.feed(MIBLE_CHAR_19, bytes.fromhex("0000000c0200"))
    machine.feed(MIBLE_CHAR_19, b"\x01\x00" + b"x" * 18)
    with pytest.raises(MiBLEAuthenticationError):
        machine.feed(MIBLE_CHAR_19, b"\x02\x00" + b"x" * 14)


def test_disconnect_midway() -> None:
    machine = MiBLEStateMachine()
    machine.start_activation()
    with pytest.raises(DisconnectedError):
        machine.disconnected()
    assert machine.phase is Phase.FAILED


class _SilentClient:
    is_connected = True

    def set_disconnected_callback(self, callback: object) -> None:
        self.callback = callback

    async def start_notify(self, uuid: str, callback: object) -> None:
        pass

    async def stop_notify(self, uuid: str) -> None:
        pass

    async def write_gatt_char(
        self, uuid: str, data: bytes, *, response: bool
    ) -> None:
        pass


def test_protocol_stage_timeout() -> None:
    async def run() -> None:
        machine = MiBLEStateMachine()
        with pytest.raises(MiBLETimeoutError):
            await run_state_machine(
                _SilentClient(),
                machine,
                machine.start_activation(),
                stage_timeout=0.001,
            )

    asyncio.run(run())


def test_transport_disconnect_event_midway() -> None:
    async def run() -> None:
        machine = MiBLEStateMachine()
        disconnected = asyncio.Event()
        disconnected.set()
        with pytest.raises(DisconnectedError):
            await run_state_machine(
                _SilentClient(),
                machine,
                machine.start_activation(),
                disconnected_event=disconnected,
            )

    asyncio.run(run())
