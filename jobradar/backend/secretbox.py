"""Encrypt per-user secrets (Gmail refresh tokens, resume files) at rest.

AES-256-GCM with the key in APP_ENCRYPTION_KEY. The user id and purpose are bound
in as associated data, so a ciphertext copied into another user's record (or used
for another purpose) fails to decrypt instead of leaking.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import env
from ..push import b64u_decode, b64u_encode

PREFIX = "v1"


class SecretBoxError(Exception):
    pass


def new_key() -> str:
    return b64u_encode(AESGCM.generate_key(bit_length=256))


def _key(key_b64: str | None = None) -> bytes:
    raw = key_b64 or env("APP_ENCRYPTION_KEY")
    if not raw:
        raise SecretBoxError("APP_ENCRYPTION_KEY is not set")
    key = b64u_decode(raw)
    if len(key) != 32:
        raise SecretBoxError("APP_ENCRYPTION_KEY must be 32 bytes (base64url); create one with `python -m jobradar new-key`")
    return key


def seal(data: bytes, *, uid: str, purpose: str, key: str | None = None) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key(key)).encrypt(nonce, data, f"{purpose}:{uid}".encode())
    return f"{PREFIX}.{b64u_encode(nonce)}.{b64u_encode(ct)}"


def open_(token: str, *, uid: str, purpose: str, key: str | None = None) -> bytes:
    try:
        prefix, nonce, ct = token.split(".")
        if prefix != PREFIX:
            raise ValueError("unknown format")
        return AESGCM(_key(key)).decrypt(b64u_decode(nonce), b64u_decode(ct), f"{purpose}:{uid}".encode())
    except SecretBoxError:
        raise
    except Exception as e:  # noqa: BLE001 - InvalidTag, bad base64...
        raise SecretBoxError("can't decrypt: wrong key, user or purpose") from e
