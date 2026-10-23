"""Backend core: ID-token verification, per-user secret encryption, Firestore encoding."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from jobradar.backend import idtoken, secretbox, store
from jobradar.push import b64u_encode

# ------------------------------------------------------------------ secret box
KEY = secretbox.new_key()


def test_secretbox_roundtrip_and_binding():
    t = secretbox.seal(b"1//refresh-token", uid="alice", purpose="gmail", key=KEY)
    assert b"refresh" not in t.encode()
    assert secretbox.open_(t, uid="alice", purpose="gmail", key=KEY) == b"1//refresh-token"
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.open_(t, uid="bob", purpose="gmail", key=KEY)       # copied into another user's record
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.open_(t, uid="alice", purpose="resume", key=KEY)    # reused for another purpose
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.open_(t, uid="alice", purpose="gmail", key=secretbox.new_key())


def test_secretbox_needs_a_valid_key(monkeypatch):
    monkeypatch.delenv("APP_ENCRYPTION_KEY", raising=False)
    with pytest.raises(secretbox.SecretBoxError, match="not set"):
        secretbox.seal(b"x", uid="a", purpose="p")
    with pytest.raises(secretbox.SecretBoxError, match="32 bytes"):
        secretbox.seal(b"x", uid="a", purpose="p", key=b64u_encode(b"short"))


# -------------------------------------------------------------------- id token
PROJECT = "job-radar-test"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def cert_pem(key):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken")])
    now = datetime.now(timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
         .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
         .sign(key, hashes.SHA256()))
    return c.public_bytes(serialization.Encoding.PEM).decode()


class CertSession:
    calls = 0
    def get(self, url):
        CertSession.calls += 1
        class R:
            headers = {"Cache-Control": "public, max-age=19000"}
            def raise_for_status(self): pass
            def json(self): return {"kid1": cert_pem(RSA_KEY)}
        return R()


def make_token(key=RSA_KEY, kid="kid1", **over):
    now = int(time.time())
    claims = {"aud": PROJECT, "iss": f"https://securetoken.google.com/{PROJECT}", "sub": "uid-123",
              "iat": now - 10, "exp": now + 3600, "auth_time": now - 10, "email": "a@b.com", "email_verified": True,
              "firebase": {"sign_in_provider": "password"}}
    claims.update(over)
    h = b64u_encode(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    c = b64u_encode(json.dumps(claims).encode())
    sig = key.sign(f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{c}.{b64u_encode(sig)}"


def test_valid_token():
    v = idtoken.Verifier(PROJECT, CertSession())
    user = v.verify(make_token())
    assert user == {"uid": "uid-123", "email": "a@b.com", "email_verified": True, "name": "", "provider": "password"}


@pytest.mark.parametrize("token,why", [
    (lambda: make_token(aud="other-project"), "another project"),
    (lambda: make_token(iss="https://evil.example"), "issuer"),
    (lambda: make_token(exp=int(time.time()) - 3600), "expired"),
    (lambda: make_token(iat=int(time.time()) + 3600), "future"),
    (lambda: make_token(sub=""), "no user id"),
    (lambda: make_token(key=OTHER_KEY), "bad signature"),
    (lambda: make_token(kid="unknown"), "unknown signing key"),
    (lambda: "not.a.token", "malformed"),
])
def test_rejected_tokens(token, why):
    with pytest.raises(idtoken.InvalidToken, match=why):
        idtoken.Verifier(PROJECT, CertSession()).verify(token())


def test_certs_are_cached():
    CertSession.calls = 0
    v = idtoken.Verifier(PROJECT, CertSession())
    for _ in range(5):
        v.verify(make_token())
    assert CertSession.calls == 1


# ----------------------------------------------------------------------- store
def test_firestore_value_roundtrip():
    data = {"s": "x", "i": 3, "f": 1.5, "b": True, "n": None, "bytes": b"\x00\x01",
            "list": [1, "a", {"k": [True]}], "map": {"nested": {"deep": 1}}}
    enc = {k: store.encode(v) for k, v in data.items()}
    assert {k: store.decode(v) for k, v in enc.items()} == data


def test_memory_store_conditional_writes():
    s = store.MemoryStore()
    s.set("c", "d", {"a": 1}, if_missing=True)
    with pytest.raises(store.Conflict):
        s.set("c", "d", {"a": 2}, if_missing=True)
    _, ver = s.get_versioned("c", "d")
    s.set("c", "d", {"a": 2}, if_version=ver)
    with pytest.raises(store.Conflict):
        s.set("c", "d", {"a": 3}, if_version=ver)  # stale version
    s.update("c", "d", {"b": 1})
    assert s.get("c", "d") == {"a": 2, "b": 1}
    assert s.list_ids("c") == ["d"]
    s.delete("c", "d")
    assert s.get("c", "d") is None


class FakeFirestore:
    def __init__(self):
        self.docs, self.calls = {}, []
    def _r(self, code, payload=None, text=""):
        class R:
            status_code = code
            def json(self_): return payload
            def raise_for_status(self_):
                if code >= 400: raise RuntimeError(code)
        R.text = text or json.dumps(payload or {})
        return R()
    def get(self, url, headers=None, params=None):
        assert headers["Authorization"] == "Bearer tok"
        name = url.split("/documents/")[1]
        if "/" not in name:  # list
            ids = sorted(k.split("/")[1] for k in self.docs if k.startswith(name + "/"))
            page = int((params or {}).get("pageToken") or 0)
            chunk = ids[page:page + 2]
            out = {"documents": [{"name": f"x/{name}/{i}"} for i in chunk]}
            if page + 2 < len(ids): out["nextPageToken"] = str(page + 2)
            return self._r(200, out)
        if name not in self.docs: return self._r(404, {})
        return self._r(200, {"fields": self.docs[name]["fields"], "updateTime": self.docs[name]["t"]})
    def patch(self, url, headers=None, params=None, json=None):
        name = url.split("/documents/")[1]
        self.calls.append((name, params))
        pre = dict(params)
        cur = self.docs.get(name)
        if "currentDocument.updateTime" in pre and (not cur or cur["t"] != pre["currentDocument.updateTime"]):
            return self._r(400, {}, "FAILED_PRECONDITION")
        mask = [p[1].strip("`") for p in params if p[0] == "updateMask.fieldPaths"]
        fields = dict(cur["fields"]) if (cur and mask) else {}
        fields.update(json["fields"])
        self.docs[name] = {"fields": fields, "t": f"t{len(self.calls)}"}
        return self._r(200, {})
    def delete(self, url, headers=None):
        self.docs.pop(url.split("/documents/")[1], None)
        return self._r(200, {})


def test_firestore_store_against_rest_shape():
    fake = FakeFirestore()
    fs = store.FirestoreStore("p", fake)
    fs._token, fs._token_exp = "tok", time.time() + 3600
    fs.set("profiles", "u1", {"skills": ["SQL"], "min_match": 70})
    fs.update("profiles", "u1", {"drafts_enabled": False})
    data, ver = fs.get_versioned("profiles", "u1")
    assert data == {"skills": ["SQL"], "min_match": 70, "drafts_enabled": False}
    with pytest.raises(store.Conflict):
        fs.set("profiles", "u1", {"x": 1}, if_version="stale")
    for i in range(5):
        fs.set("profiles", f"z{i}", {"i": i})
    assert len(fs.list_ids("profiles")) == 6   # paged
    fs.delete("profiles", "u1")
    assert fs.get("profiles", "u1") is None
