"""Encrypted status sync file shared with the app (docs/sync.js has the same format).

sync/state.enc.json = {"v":1,"kdf":"PBKDF2-SHA256","iter":N,"salt","iv","ct","updated"}
where ct = AES-256-GCM(key = PBKDF2-SHA256(passphrase, salt, N), JSON state).
State: {"statuses":{id:{status,at,appliedAt?,source?}}, "saved":{id:job}, "sent":{id:iso},
        "pending":{id:{at,open}}, "gmail":{last_check, processed:[...], events:[...]}}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import ROOT
from .push import b64u_decode, b64u_encode  # noqa: F401 (re-exported for tests)

PATH = ROOT / "sync" / "state.enc.json"
ITERATIONS = 310000
KEEP = ("later", "applied", "interview", "offer", "rejected")


class WrongPassphrase(Exception):
    pass


def derive_key(passphrase: str, salt_b64u: str, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b64u_decode(salt_b64u), iterations=iterations)
    return kdf.derive(passphrase.encode())


def seal(state: dict, key: bytes, salt: str, iterations: int = ITERATIONS) -> dict:
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(state, separators=(",", ":")).encode(), None)
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": salt, "iv": b64u_encode(iv),
            "ct": b64u_encode(ct), "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def open_envelope(envelope: dict, passphrase: str) -> tuple[dict, bytes]:
    key = derive_key(passphrase, envelope["salt"], int(envelope.get("iter", ITERATIONS)))
    try:
        pt = AESGCM(key).decrypt(b64u_decode(envelope["iv"]), b64u_decode(envelope["ct"]), None)
    except Exception as e:  # noqa: BLE001 - InvalidTag
        raise WrongPassphrase("SYNC_PASSPHRASE doesn't match the one used in the app") from e
    return normalize(json.loads(pt)), key


def load(passphrase: str, path: Path = PATH) -> tuple[dict, dict | None, bytes | None]:
    """(state, envelope, key). A missing file means the app hasn't been signed in yet."""
    if not path.exists():
        return normalize({}), None, None
    envelope = json.loads(path.read_text(encoding="utf-8"))
    state, key = open_envelope(envelope, passphrase)
    return state, envelope, key


def save(state: dict, envelope: dict, key: bytes, path: Path = PATH) -> None:
    out = seal(state, key, envelope["salt"], int(envelope.get("iter", ITERATIONS)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


def normalize(s: dict | None) -> dict:
    s = dict(s or {})
    for k in ("statuses", "saved", "sent", "pending", "gmail"):
        s[k] = dict(s.get(k) or {})
    return s


def merge(a: dict, b: dict) -> dict:
    """Identical rules to JRSync.merge in docs/sync.js."""
    a, b = normalize(a), normalize(b)
    out = normalize({})
    for jid in set(a["statuses"]) | set(b["statuses"]):
        x, y = a["statuses"].get(jid), b["statuses"].get(jid)
        win = y if not x else x if not y else (y if str(y.get("at", "")) > str(x.get("at", "")) else x)
        applied = sorted(v for v in ((x or {}).get("appliedAt"), (y or {}).get("appliedAt")) if v)
        rec = dict(win)
        rec.pop("appliedAt", None)
        if applied:
            rec["appliedAt"] = applied[0]
        out["statuses"][jid] = rec
    for jid in set(a["pending"]) | set(b["pending"]):
        x, y = a["pending"].get(jid), b["pending"].get(jid)
        out["pending"][jid] = y if not x else x if not y else (y if str(y["at"]) > str(x["at"]) else x)
    for jid in set(a["sent"]) | set(b["sent"]):
        out["sent"][jid] = max(v for v in (a["sent"].get(jid), b["sent"].get(jid)) if v)
    for jid in set(a["saved"]) | set(b["saved"]):
        if (out["statuses"].get(jid) or {}).get("status") in KEEP:
            out["saved"][jid] = b["saved"].get(jid) or a["saved"].get(jid)
    ga, gb = a["gmail"], b["gmail"]
    out["gmail"] = gb if str(gb.get("last_check", "")) >= str(ga.get("last_check", "")) else ga
    return out
