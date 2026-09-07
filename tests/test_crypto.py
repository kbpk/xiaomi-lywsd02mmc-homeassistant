"""Vectors independently reproducible with WebCrypto/SJCL reference inputs."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from custom_components.lywsd02mmc_local.crypto import (
    AuthenticationTagError,
    decrypt_device_id,
    derive_login_material,
    derive_setup_material,
    encrypt_device_id,
    p256_public_key_bytes,
    p256_shared_secret,
)

SHARED = bytes(range(1, 33))
SETUP_DERIVED = bytes.fromhex(
    "86a873927f90da962ec5e66bc6ef32a21cb2c16c9b943645356e7eb4edbec74d"
    "4982eb34e2eba9a2ee02ff9f149bd0916398c6cba1ed4f64948d3d2ea8b664c8"
)


def test_setup_hkdf_and_slices() -> None:
    material = derive_setup_material(SHARED)
    assert (
        material.token
        + material.bindkey
        + material.device_id_key
        + material.reserved
        == SETUP_DERIVED
    )
    assert material.token.hex() == "86a873927f90da962ec5e66b"
    assert material.bindkey.hex() == "c6ef32a21cb2c16c9b943645356e7eb4"
    assert material.device_id_key.hex() == "edbec74d4982eb34e2eba9a2ee02ff9f"


def test_p256_ecdh_fixed_private_keys() -> None:
    private_a = ec.derive_private_key(1, ec.SECP256R1())
    private_b = ec.derive_private_key(2, ec.SECP256R1())
    public_b = p256_public_key_bytes(private_b)
    assert public_b.hex() == (
        "047cf27b188d034f7e8a52380304b51ac3c08969e277f21b35a60b48fc47669978"
        "07775510db8ed040293d9ac69f7430dbba7dade63ce982299e04b79d227873d1"
    )
    assert p256_shared_secret(private_a, public_b).hex() == (
        "7cf27b188d034f7e8a52380304b51ac3c08969e277f21b35a60b48fc47669978"
    )
    assert p256_shared_secret(private_b, p256_public_key_bytes(private_a)) == (
        p256_shared_secret(private_a, public_b)
    )


def test_login_derivation_vector() -> None:
    material = derive_setup_material(SHARED)
    login = derive_login_material(
        material.token, bytes(range(16)), bytes(range(16, 32))
    )
    assert login.derived.hex() == (
        "6f02a6a2432acf530ad0c89a41b4c37c2efa704bf90bb6d90734f0dbd98cb85b"
        "5b78cd566711d194a5f78f6e53cb6df719a5cdcafb7d56fa0887364191a54ae0"
    )
    assert login.expected_device_proof.hex() == (
        "5a335a70429e5e612de90872ad95cb3d3a78c3dcacd8c92546cb3d84e69d324f"
    )
    assert login.client_proof.hex() == (
        "638ed0d8a06baed5babae02f40e9f2754f1fb9ee63d0c79592d8cfcbff608228"
    )


def test_device_id_aes_ccm_vector_and_invalid_tag() -> None:
    key = derive_setup_material(SHARED).device_id_key
    device_id = b"\x00blt.3.129vABC123ATC"
    ciphertext = encrypt_device_id(device_id, key)
    assert ciphertext.hex() == "0c4e4458446eb9c59650f066f74e493b6748277419988939"
    assert decrypt_device_id(ciphertext, key) == device_id
    damaged = ciphertext[:-1] + bytes((ciphertext[-1] ^ 1,))
    with pytest.raises(AuthenticationTagError):
        decrypt_device_id(damaged, key)


@pytest.mark.parametrize("bad_length", [b"", b"x" * 11, b"x" * 13])
def test_login_rejects_invalid_token_length(bad_length: bytes) -> None:
    with pytest.raises(ValueError):
        derive_login_material(bad_length, b"a" * 16, b"b" * 16)
