"""GitHub Actions -> Firebase: service-account auth, user lookup, conditional writes, gmail-sync."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from jobradar import cloud_store
from jobradar.push import b64u_decode

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SA = {"type": "service_account", "project_id": "job-radar-test", "private_key_id": "k1",
      "client_email": "bot@job-radar-test.iam.gserviceaccount.com",
      "private_key": KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode()}


def test_jwt_is_valid_rs256():
    token = cloud_store.signed_jwt(SA, now=1_800_000_000)
    head, claims, sig = token.split(".")
    c = json.loads(b64u_decode(claims))
    assert c["iss"] == SA["client_email"] and c["aud"] == cloud_store.TOKEN_URL and c["exp"] - c["iat"] == 3600
    KEY.public_key().verify(b64u_decode(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_service_account_validation():
    with pytest.raises(ValueError):
        cloud_store.load_service_account(json.dumps({"project_id": "x"}))


class Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code, self._p, self.text = code, payload, text or json.dumps(payload)
    def json(self):
        return self._p
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeGoogle:
    """oauth2 + identitytoolkit + firestore + gmail, enough for gmail-sync."""
    def __init__(self, doc_state=None, conflicts=0):
        self.doc = None if doc_state is None else {"state": json.dumps(doc_state), "updateTime": "t1"}
        self.conflicts, self.writes = conflicts, []

    def post(self, url, data=None, json=None, headers=None, **kw):
        if url == cloud_store.TOKEN_URL:  # same endpoint for both: service account vs Gmail refresh
            if data["grant_type"].endswith("jwt-bearer"):
                return Resp(200, {"access_token": "fs-token"})
            assert data["grant_type"] == "refresh_token"
            return Resp(200, {"access_token": "gmail-token"})
        if "accounts:lookup" in url:
            return Resp(200, {"users": [{"localId": "uid123", "email": json["email"][0]}]} if json["email"][0] == "chakri.evn@gmail.com" else {})
        raise AssertionError(url)

    def get(self, url, headers=None, params=None, **kw):
        if "firestore" in url:
            assert url.endswith("/documents/users/uid123") and headers["Authorization"] == "Bearer fs-token"
            if not self.doc:
                return Resp(404, {})
            return Resp(200, {"fields": {"state": {"stringValue": self.doc["state"]}}, "updateTime": self.doc["updateTime"]})
        if url.endswith("/messages"):
            return Resp(200, {"messages": [{"id": "m1"}]})
        return Resp(200, {"id": "m1", "threadId": "t1", "snippet": "", "internalDate": "1792900000000",
                          "payload": {"headers": [{"name": "Subject", "value": "Your application was sent to Zyntrix Analytics Pvt Ltd"},
                                                  {"name": "From", "value": "LinkedIn <jobs-noreply@linkedin.com>"}]}})

    def patch(self, url, headers=None, params=None, json=None):
        pre = dict(params)
        if self.conflicts:
            self.conflicts -= 1
            self.doc = {"state": self.doc["state"] if self.doc else "{}", "updateTime": "t-new"}
            return Resp(400, {"error": {"status": "FAILED_PRECONDITION"}})
        if self.doc:
            assert pre.get("currentDocument.updateTime") == self.doc["updateTime"]
        else:
            assert pre.get("currentDocument.exists") == "false"
        self.writes.append(json)
        self.doc = {"state": json["fields"]["state"]["stringValue"], "updateTime": "t2"}
        return Resp(200, {})


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", json.dumps(SA))
    for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")


def run_cli(monkeypatch, fake):
    from jobradar import __main__ as cli
    monkeypatch.setattr(cli, "make_session", lambda *a, **k: fake)
    return cli.main(["gmail-sync"])


def test_gmail_sync_writes_to_firestore(env, monkeypatch, capsys):
    fake = FakeGoogle(doc_state={"statuses": {"keep": {"status": "applied", "at": "2026-10-20T00:00:00Z"}}})
    assert run_cli(monkeypatch, fake) == 0
    state = json.loads(fake.doc["state"])
    assert state["statuses"]["keep"]["status"] == "applied"                     # your own statuses untouched
    added = [k for k, v in state["statuses"].items() if v.get("source") == "gmail"]
    assert len(added) == 1 and state["saved"][added[0]]["company"] == "Zyntrix Analytics Pvt Ltd"
    assert fake.writes[0]["fields"]["email"]["stringValue"] == "chakri.evn@gmail.com"
    assert "Gmail status sync" in capsys.readouterr().out


def test_gmail_sync_retries_when_the_app_saves_at_the_same_time(env, monkeypatch):
    fake = FakeGoogle(doc_state={}, conflicts=1)
    assert run_cli(monkeypatch, fake) == 0
    assert len(fake.writes) == 1  # second attempt, against the fresh updateTime


def test_gmail_sync_skips_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    from jobradar import __main__ as cli
    assert cli.main(["gmail-sync"]) == 0
    assert "skipped" in capsys.readouterr().out
