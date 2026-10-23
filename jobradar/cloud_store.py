"""Read and update your statuses in Firebase (Firestore) from GitHub Actions.

Uses a Firebase service account (JSON key in the FIREBASE_SERVICE_ACCOUNT secret)
over plain HTTPS: a signed JWT is swapped for an access token, then Firestore's
REST API reads users/<uid>. Writes are conditional on the document not having
changed since it was read, so a phone saving at the same moment is never lost.
"""

from __future__ import annotations

import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .push import b64u_encode

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class Conflict(Exception):
    """The document changed between our read and our write: read again."""


def load_service_account(raw: str) -> dict:
    sa = json.loads(raw)
    for k in ("client_email", "private_key", "project_id"):
        if not sa.get(k):
            raise ValueError(f"FIREBASE_SERVICE_ACCOUNT is missing '{k}'")
    return sa


def signed_jwt(sa: dict, now: int | None = None) -> str:
    now = int(now or time.time())
    head = b64u_encode(json.dumps({"alg": "RS256", "typ": "JWT", "kid": sa.get("private_key_id", "")}).encode())
    claims = b64u_encode(json.dumps({"iss": sa["client_email"], "scope": SCOPE, "aud": TOKEN_URL,
                                     "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{claims}.{b64u_encode(sig)}"


def access_token(session, sa: dict) -> str:
    r = session.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                      "assertion": signed_jwt(sa)})
    r.raise_for_status()
    return r.json()["access_token"]


def lookup_uid(session, token: str, project: str, email: str) -> str | None:
    """The Firebase user id for the email you sign in to the app with."""
    r = session.post(f"https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:lookup",
                     headers={"Authorization": f"Bearer {token}"}, json={"email": [email]})
    r.raise_for_status()
    users = r.json().get("users") or []
    return users[0]["localId"] if users else None


def _doc_url(project: str, uid: str) -> str:
    return f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/users/{uid}"


def get_state(session, token: str, project: str, uid: str) -> tuple[dict | None, str | None]:
    """(state, updateTime) or (None, None) when you haven't signed in to the app yet."""
    r = session.get(_doc_url(project, uid), headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    doc = r.json()
    raw = ((doc.get("fields") or {}).get("state") or {}).get("stringValue") or "{}"
    return json.loads(raw), doc.get("updateTime")


def put_state(session, token: str, project: str, uid: str, state: dict, update_time: str | None, email: str = "") -> None:
    params = [("updateMask.fieldPaths", "state"), ("updateMask.fieldPaths", "email")]
    params.append(("currentDocument.updateTime", update_time) if update_time else ("currentDocument.exists", "false"))
    body = {"fields": {"state": {"stringValue": json.dumps(state, separators=(",", ":"))},
                       "email": {"stringValue": email}}}
    r = session.patch(_doc_url(project, uid), headers={"Authorization": f"Bearer {token}"}, params=params, json=body)
    if r.status_code == 409 or (r.status_code in (400, 412) and "FAILED_PRECONDITION" in r.text):
        raise Conflict()
    r.raise_for_status()
