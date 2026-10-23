"""Your resume, encrypted, so the public repo can carry it without exposing it.

`python -m jobradar encrypt-resume my_resume.pdf` writes assets/resume.pdf.enc
(AES-256-GCM) and prints a key for the RESUME_KEY GitHub Secret. Without the
key the file is unreadable; only the workflow can decrypt it to attach to drafts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import ROOT, env
from .push import b64u_decode, b64u_encode

ENC_PATH = ROOT / "assets" / "resume.pdf.enc"
MAGIC = b"JRRES1"


def encrypt_file(pdf: Path, out: Path = ENC_PATH, attach_name: str | None = None) -> str:
    data = pdf.read_bytes()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"{pdf} doesn't look like a PDF")
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    header = json.dumps({"name": attach_name or pdf.name}).encode()
    blob = AESGCM(key).encrypt(nonce, data, header)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(MAGIC + len(header).to_bytes(2, "big") + header + nonce + blob)
    return b64u_encode(key)


def decrypt_file(key_b64: str, path: Path = ENC_PATH) -> tuple[str, bytes]:
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("not a Job Radar resume file")
    n = int.from_bytes(raw[6:8], "big")
    header = raw[8:8 + n]
    nonce, blob = raw[8 + n:20 + n], raw[20 + n:]
    data = AESGCM(b64u_decode(key_b64)).decrypt(nonce, blob, header)
    return json.loads(header)["name"], data


def load() -> tuple[str, bytes] | None:
    """(filename, pdf bytes) if both the encrypted file and RESUME_KEY exist, else None."""
    key = env("RESUME_KEY")
    if not key or not ENC_PATH.exists():
        return None
    return decrypt_file(key)
