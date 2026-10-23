"""Standard Web Push (RFC 8291 encryption + RFC 8292 VAPID) using only `cryptography`.

This is what lets GitHub Actions send a notification straight to the Job Radar
app on your phone, with no server and no third-party service: the browser's own
push service (Google's for Chrome/Android, Apple's for iPhone) delivers it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


class PushGone(Exception):
    """The subscription expired or was revoked (HTTP 404/410). Re-enable alerts in the app."""


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(data: str) -> bytes:
    data = data.strip()
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# --------------------------------------------------------------------- keys
def generate_vapid_keys() -> tuple[str, str]:
    """Return (private_key, public_key), both base64url. Private goes in GitHub Secrets."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return b64u_encode(raw), b64u_encode(_public_bytes(key.public_key()))


def load_private_key(private_b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64u_decode(private_b64), "big"), ec.SECP256R1())


def public_key_for(private_b64: str) -> str:
    return b64u_encode(_public_bytes(load_private_key(private_b64).public_key()))


# -------------------------------------------------------------------- VAPID
def vapid_header(endpoint: str, private_b64: str, subject: str, ttl_s: int = 12 * 3600) -> str:
    key = load_private_key(private_b64)
    parts = urlsplit(endpoint)
    header = b64u_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u_encode(json.dumps(
        {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + ttl_s, "sub": subject},
        separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64u_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={b64u_encode(_public_bytes(key.public_key()))}"


# --------------------------------------------------------------- encryption
def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """aes128gcm content encoding (RFC 8188) with Web Push key derivation (RFC 8291)."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    salt = salt or os.urandom(16)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(sender_key.public_key())

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = sender_key.exchange(ec.ECDH(), ua_key)

    # RFC 8291 section 3.4
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("push payload too large; keep it under ~3 KB")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = last record
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


# --------------------------------------------------------------------- send
def send(subscription: dict, payload: dict, private_b64: str, subject: str, session,
         ttl: int = 24 * 3600, urgency: str = "high") -> int:
    """POST one encrypted notification. Raises PushGone if the subscription is dead."""
    endpoint = subscription["endpoint"]
    keys = subscription.get("keys") or {}
    body = encrypt(json.dumps(payload, separators=(",", ":")).encode(), keys["p256dh"], keys["auth"])
    r = session.post(
        endpoint,
        data=body,
        headers={
            "Authorization": vapid_header(endpoint, private_b64, subject),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(ttl),
            "Urgency": urgency,
        },
    )
    if r.status_code in (404, 410):
        raise PushGone(f"subscription gone (HTTP {r.status_code})")
    r.raise_for_status()
    return r.status_code
