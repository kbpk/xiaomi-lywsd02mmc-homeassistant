"""Pure MiBLE cryptographic primitives.

Home Assistant Core 2026 pins ``cryptography`` as a direct runtime dependency.
The integration therefore declares no requirement of its own.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SETUP_INFO = b"mible-setup-info"
LOGIN_INFO = b"mible-login-info"
DEVICE_ID_NONCE = bytes.fromhex("101112131415161718191a1b")
DEVICE_ID_AAD = b"devID"


class AuthenticationTagError(ValueError):
    """Raised when an authenticated ciphertext cannot be verified."""


@dataclass(frozen=True, slots=True)
class SetupMaterial:
    """Fields in the 64-byte MiBLE setup HKDF output."""

    token: bytes
    bindkey: bytes
    device_id_key: bytes
    reserved: bytes


@dataclass(frozen=True, slots=True)
class LoginMaterial:
    """Mutual authentication proofs for one login exchange."""

    expected_device_proof: bytes
    client_proof: bytes
    derived: bytes


def hkdf_sha256(
    key_material: bytes, *, length: int, info: bytes, salt: bytes | None = None
) -> bytes:
    """Derive bytes using RFC 5869 HKDF-SHA256."""

    if not key_material:
        raise ValueError("key material must not be empty")
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=salt, info=info
    ).derive(key_material)


def generate_p256_private_key() -> ec.EllipticCurvePrivateKey:
    """Generate an ephemeral P-256 key; it must never be persisted."""

    return ec.generate_private_key(ec.SECP256R1())


def p256_public_key_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """Return an uncompressed SEC1 public key (04 || X || Y)."""

    return private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def p256_shared_secret(
    private_key: ec.EllipticCurvePrivateKey, peer_public_key: bytes
) -> bytes:
    """Perform P-256 ECDH with a validated uncompressed peer point."""

    if len(peer_public_key) != 65 or peer_public_key[0] != 0x04:
        raise ValueError("peer public key must be a 65-byte uncompressed P-256 point")
    peer = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), peer_public_key
    )
    return private_key.exchange(ec.ECDH(), peer)


def derive_setup_material(shared_secret: bytes) -> SetupMaterial:
    """Derive token, bindkey and DID encryption key as TelinkFlasher does."""

    derived = hkdf_sha256(shared_secret, length=64, info=SETUP_INFO)
    return SetupMaterial(
        token=derived[0:12],
        bindkey=derived[12:28],
        device_id_key=derived[28:44],
        reserved=derived[44:64],
    )


def encrypt_device_id(device_id: bytes, key: bytes) -> bytes:
    """Encrypt and authenticate the registration DID with a four-byte tag."""

    if len(key) != 16:
        raise ValueError("device ID key must be 16 bytes")
    return AESCCM(key, tag_length=4).encrypt(
        DEVICE_ID_NONCE, device_id, DEVICE_ID_AAD
    )


def decrypt_device_id(ciphertext_and_tag: bytes, key: bytes) -> bytes:
    """Decrypt a registration DID, rejecting an invalid tag."""

    try:
        return AESCCM(key, tag_length=4).decrypt(
            DEVICE_ID_NONCE, ciphertext_and_tag, DEVICE_ID_AAD
        )
    except InvalidTag as err:
        raise AuthenticationTagError("invalid device ID authentication tag") from err


def derive_login_material(
    token: bytes, client_random: bytes, device_random: bytes
) -> LoginMaterial:
    """Derive both MiBLE mutual-login HMAC proofs.

    Salt ordering and HMAC messages deliberately differ, matching the working
    TelinkFlasher implementation.
    """

    if len(token) != 12:
        raise ValueError("Mi token must be 12 bytes")
    if len(client_random) != 16 or len(device_random) != 16:
        raise ValueError("MiBLE login random values must be 16 bytes")
    client_device = client_random + device_random
    device_client = device_random + client_random
    derived = hkdf_sha256(
        token, length=64, salt=client_device, info=LOGIN_INFO
    )
    return LoginMaterial(
        expected_device_proof=hmac.new(
            derived[0:16], device_client, hashlib.sha256
        ).digest(),
        client_proof=hmac.new(
            derived[16:32], client_device, hashlib.sha256
        ).digest(),
        derived=derived,
    )


def aes_ccm_decrypt(
    key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes
) -> bytes:
    """Authenticated AES-CCM decryption with an explicit four-byte tag."""

    if len(key) != 16:
        raise ValueError("AES-CCM key must be 16 bytes")
    if len(tag) != 4:
        raise ValueError("MiBeacon authentication tag must be 4 bytes")
    try:
        return AESCCM(key, tag_length=4).decrypt(nonce, ciphertext + tag, aad)
    except InvalidTag as err:
        raise AuthenticationTagError("invalid AES-CCM authentication tag") from err


def aes_ccm_encrypt(
    key: bytes, nonce: bytes, plaintext: bytes, aad: bytes
) -> tuple[bytes, bytes]:
    """AES-CCM encryption helper returning ciphertext and tag separately."""

    encrypted = AESCCM(key, tag_length=4).encrypt(nonce, plaintext, aad)
    return encrypted[:-4], encrypted[-4:]
