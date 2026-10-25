"""Document storage used by the backend: Firestore in production, memory in tests.

Collections (see firestore.rules for who may read what):
  users/{uid}            the app's synced state: statuses, Gmail events + review
                         list, review answers, draft records    (client read/write own)
  profiles/{uid}         confirmed profile + preferences (threshold...)   (client read own)
  matches/{uid}          the user's scored jobs + explanations            (client read own)
  gmail/{uid}            Gmail connection + last sync status, no secrets  (client read own)
  secrets/{uid}          encrypted Gmail refresh token                    (server only)
  resumes/{uid}          resume file info; resume_chunks/{uid}_{n} the encrypted file (server only)
  push/{uid}             web push subscriptions                           (server only)
  oauth_states/{id}      pending Gmail connections (state + PKCE verifier) (server only)
  pool/{n}               the shared job pool, in chunks                   (server only)
"""

from __future__ import annotations

import base64
import copy
import itertools
import threading
import time
from datetime import datetime, timezone


class Conflict(Exception):
    """The document changed since it was read."""


class Store:
    def get(self, collection: str, doc_id: str) -> dict | None:
        return self.get_versioned(collection, doc_id)[0]

    def get_versioned(self, collection: str, doc_id: str) -> tuple[dict | None, str | None]:
        raise NotImplementedError

    def set(self, collection: str, doc_id: str, data: dict, *, if_version: str | None = None, if_missing: bool = False) -> None:
        raise NotImplementedError

    def update(self, collection: str, doc_id: str, fields: dict) -> None:
        """Merge top-level fields (creates the document if needed)."""
        raise NotImplementedError

    def delete(self, collection: str, doc_id: str) -> None:
        raise NotImplementedError

    def list_ids(self, collection: str) -> list[str]:
        raise NotImplementedError


def _firestore_shape(v, in_list: bool = False) -> None:
    """Reject what Firestore rejects, so tests catch it: a list directly inside a list."""
    if isinstance(v, dict):
        for x in v.values():
            _firestore_shape(x)
    elif isinstance(v, (list, tuple)):
        if in_list:
            raise ValueError("Firestore can't store a list inside a list")
        for x in v:
            _firestore_shape(x, True)


class MemoryStore(Store):
    def __init__(self):
        self.docs: dict[tuple[str, str], tuple[dict, str]] = {}
        self._clock = itertools.count(1)
        self._lock = threading.Lock()

    def get_versioned(self, collection, doc_id):
        with self._lock:
            hit = self.docs.get((collection, doc_id))
            return (copy.deepcopy(hit[0]), hit[1]) if hit else (None, None)

    def set(self, collection, doc_id, data, *, if_version=None, if_missing=False):
        with self._lock:
            cur = self.docs.get((collection, doc_id))
            if if_missing and cur:
                raise Conflict()
            if if_version is not None and (not cur or cur[1] != if_version):
                raise Conflict()
            _firestore_shape(data)
            self.docs[(collection, doc_id)] = (copy.deepcopy(data), f"v{next(self._clock)}")

    def update(self, collection, doc_id, fields):
        _firestore_shape(fields)
        with self._lock:
            cur = self.docs.get((collection, doc_id))
            data = copy.deepcopy(cur[0]) if cur else {}
            data.update(copy.deepcopy(fields))
            self.docs[(collection, doc_id)] = (data, f"v{next(self._clock)}")

    def delete(self, collection, doc_id):
        with self._lock:
            self.docs.pop((collection, doc_id), None)

    def list_ids(self, collection):
        with self._lock:
            return sorted(d for c, d in self.docs if c == collection)


# ------------------------------------------------------------------ Firestore
def encode(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (bytes, bytearray)):
        return {"bytesValue": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, datetime):
        return {"timestampValue": v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [encode(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {str(k): encode(x) for k, x in v.items()}}}
    raise TypeError(f"can't store {type(v).__name__}")


def decode(v: dict):
    (kind, val), = v.items()
    if kind == "nullValue":
        return None
    if kind == "booleanValue":
        return bool(val)
    if kind == "integerValue":
        return int(val)
    if kind == "doubleValue":
        return float(val)
    if kind in ("stringValue", "referenceValue"):
        return val
    if kind == "timestampValue":
        return val
    if kind == "bytesValue":
        return base64.b64decode(val)
    if kind == "arrayValue":
        return [decode(x) for x in (val or {}).get("values", [])]
    if kind == "mapValue":
        return {k: decode(x) for k, x in ((val or {}).get("fields") or {}).items()}
    if kind == "geoPointValue":
        return val
    raise ValueError(kind)


class FirestoreStore(Store):
    """Firestore REST API with a service account (or Cloud Run's built-in identity)."""

    def __init__(self, project_id: str, session, service_account: dict | None = None):
        self.project = project_id
        self.session = session
        self.sa = service_account
        self._token, self._token_exp = None, 0.0
        self.base = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents"

    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 120:
            return self._token
        if self.sa:
            from ..cloud_store import access_token
            self._token, self._token_exp = access_token(self.session, self.sa), time.time() + 3300
        else:  # Cloud Run / GCE metadata server
            r = self.session.get("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                                 headers={"Metadata-Flavor": "Google"})
            r.raise_for_status()
            j = r.json()
            self._token, self._token_exp = j["access_token"], time.time() + int(j.get("expires_in", 3000))
        return self._token

    def _h(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def get_versioned(self, collection, doc_id):
        r = self.session.get(f"{self.base}/{collection}/{doc_id}", headers=self._h())
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        doc = r.json()
        return {k: decode(v) for k, v in (doc.get("fields") or {}).items()}, doc.get("updateTime")

    def _patch(self, collection, doc_id, data, params):
        r = self.session.patch(f"{self.base}/{collection}/{doc_id}", headers=self._h(), params=params,
                               json={"fields": {k: encode(v) for k, v in data.items()}})
        if r.status_code == 409 or (r.status_code in (400, 412) and "FAILED_PRECONDITION" in r.text):
            raise Conflict()
        if r.status_code == 404 and any(p[0] == "currentDocument.updateTime" for p in params):
            raise Conflict()
        r.raise_for_status()

    def set(self, collection, doc_id, data, *, if_version=None, if_missing=False):
        params = []
        if if_version:
            params.append(("currentDocument.updateTime", if_version))
        elif if_missing:
            params.append(("currentDocument.exists", "false"))
        self._patch(collection, doc_id, data, params)

    def update(self, collection, doc_id, fields):
        self._patch(collection, doc_id, fields, [("updateMask.fieldPaths", f"`{k}`") for k in fields])

    def delete(self, collection, doc_id):
        r = self.session.delete(f"{self.base}/{collection}/{doc_id}", headers=self._h())
        if r.status_code not in (200, 404):
            r.raise_for_status()

    def list_ids(self, collection):
        ids, token = [], None
        while True:
            params = {"pageSize": 300, "mask.fieldPaths": "__name__"}
            if token:
                params["pageToken"] = token
            r = self.session.get(f"{self.base}/{collection}", headers=self._h(), params=params)
            r.raise_for_status()
            j = r.json()
            ids += [d["name"].rsplit("/", 1)[1] for d in j.get("documents", [])]
            token = j.get("nextPageToken")
            if not token:
                return ids

    def delete_auth_user(self, uid: str) -> None:
        r = self.session.post(f"https://identitytoolkit.googleapis.com/v1/projects/{self.project}/accounts:delete",
                              headers=self._h(), json={"localId": uid})
        if r.status_code not in (200, 404):
            r.raise_for_status()
