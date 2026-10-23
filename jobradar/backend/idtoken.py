"""Verify Firebase Authentication ID tokens (what the app sends as `Authorization: Bearer`).

Checks the RS256 signature against Google's published certificates and the
claims Firebase documents: aud = project id, iss = securetoken.google.com/<project>,
not expired, issued in the past, non-empty sub (the user id).
"""

from __future__ import annotations

import json
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from ..push import b64u_decode

CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
LEEWAY = 60  # seconds of clock skew allowed


class InvalidToken(Exception):
    pass


class Verifier:
    def __init__(self, project_id: str, session, certs_url: str = CERTS_URL):
        self.project_id = project_id
        self.session = session
        self.certs_url = certs_url
        self._certs: dict = {}
        self._expires = 0.0
        self._lock = threading.Lock()

    def _keys(self, force: bool = False) -> dict:
        with self._lock:
            if force or not self._certs or time.time() > self._expires:
                r = self.session.get(self.certs_url)
                r.raise_for_status()
                self._certs = {kid: x509.load_pem_x509_certificate(pem.encode()).public_key() for kid, pem in r.json().items()}
                max_age = 3600
                for part in (r.headers.get("Cache-Control") or "").split(","):
                    if part.strip().startswith("max-age="):
                        max_age = int(part.split("=")[1])
                self._expires = time.time() + max_age
            return self._certs

    def verify(self, token: str, now: float | None = None) -> dict:
        try:
            head_b, claims_b, sig_b = token.split(".")
            head = json.loads(b64u_decode(head_b))
            claims = json.loads(b64u_decode(claims_b))
            sig = b64u_decode(sig_b)
        except Exception as e:  # noqa: BLE001
            raise InvalidToken("malformed token") from e
        if head.get("alg") != "RS256":
            raise InvalidToken("unexpected algorithm")
        keys = self._keys()
        key = keys.get(head.get("kid")) or self._keys(force=True).get(head.get("kid"))
        if key is None:
            raise InvalidToken("unknown signing key")
        try:
            key.verify(sig, f"{head_b}.{claims_b}".encode(), padding.PKCS1v15(), hashes.SHA256())
        except Exception as e:  # noqa: BLE001
            raise InvalidToken("bad signature") from e
        now = now or time.time()
        if claims.get("aud") != self.project_id:
            raise InvalidToken("token is for another project")
        if claims.get("iss") != f"https://securetoken.google.com/{self.project_id}":
            raise InvalidToken("unexpected issuer")
        if not claims.get("sub") or len(claims["sub"]) > 128:
            raise InvalidToken("no user id")
        if claims.get("exp", 0) < now - LEEWAY:
            raise InvalidToken("token expired")
        if claims.get("iat", now + 3600) > now + LEEWAY or claims.get("auth_time", 0) > now + LEEWAY:
            raise InvalidToken("token issued in the future")
        return {"uid": claims["sub"], "email": claims.get("email", ""), "email_verified": bool(claims.get("email_verified")),
                "name": claims.get("name", ""), "provider": (claims.get("firebase") or {}).get("sign_in_provider", "")}
