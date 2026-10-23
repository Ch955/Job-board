"""API: every route checks the Firebase token and only touches that user's data."""

import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from jobradar import gmail
from jobradar.backend import api, secretbox
from jobradar.backend.idtoken import InvalidToken
from jobradar.backend.store import MemoryStore
from jobradar.config import load_config
from tests.resume_fixtures import pdf_bytes

TOKENS = {"tok-alice": {"uid": "alice", "email": "alice@example.com", "email_verified": True},
          "tok-bob": {"uid": "bob", "email": "bob@example.com", "email_verified": True},
          "tok-unverified": {"uid": "carol", "email": "carol@example.com", "email_verified": False}}


class FakeVerifier:
    def verify(self, token):
        if token not in TOKENS:
            raise InvalidToken("bad")
        return TOKENS[token]


class Resp:
    def __init__(self, p, status=200): self.p, self.status_code, self.text = p, status, ""
    def json(self): return self.p
    def raise_for_status(self): pass


class Google:
    def __init__(self): self.drafts, self.revoked = [], []
    def post(self, url, data=None, json=None, headers=None, **kw):
        if url == gmail.REVOKE_URL:
            self.revoked.append(data["token"]); return Resp({})
        if url.endswith("/drafts"):
            self.drafts.append(json); return Resp({"id": f"d{len(self.drafts)}", "message": {"id": "m", "threadId": "t"}})
        if data["grant_type"] == "authorization_code":
            return Resp({"access_token": "at", "refresh_token": "rt-" + data["code"], "scope": " ".join(gmail.WEB_SCOPES)})
        return Resp({"access_token": "at"})
    def get(self, url, headers=None, params=None):
        return Resp({"emailAddress": "alice@gmail.com"})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", secretbox.new_key())
    settings = api.Settings(project_id="p", app_url="https://app.example", api_url="https://api.example",
                            allowed_origins=["https://app.example"], gmail_client_id="cid", gmail_client_secret="csecret",
                            cron_secret="cron-s3cret")
    store, g = MemoryStore(), Google()
    app = api.create_app(settings, store, g, FakeVerifier(), load_config())
    return TestClient(app), store, g


def H(tok): return {"Authorization": f"Bearer {tok}"}


PROFILE = {"full_name": "Alice", "email": "alice@example.com", "target_roles": ["Data Engineer"],
           "skills": ["Airflow", "dbt"], "years_experience": 3, "threshold": 75}


def test_requires_sign_in(env):
    c, _, _ = env
    for method, path in (("get", "/api/me"), ("put", "/api/profile"), ("post", "/api/resume"), ("delete", "/api/account"),
                         ("post", "/api/gmail/connect"), ("post", "/api/drafts"), ("get", "/api/matches")):
        assert getattr(c, method)(path).status_code == 401
        assert getattr(c, method)(path, headers=H("forged")).status_code == 401
    assert c.get("/healthz").json()["ok"]


def test_unverified_email_cant_connect_gmail(env):
    c, _, _ = env
    assert c.post("/api/gmail/connect", headers=H("tok-unverified")).status_code == 403
    assert c.get("/api/me", headers=H("tok-unverified")).json()["email_verified"] is False


def test_profile_is_per_user_and_validated(env):
    c, store, _ = env
    r = c.put("/api/profile", headers=H("tok-alice"), json=PROFILE)
    assert r.status_code == 200 and r.json()["profile"]["threshold"] == 75
    assert c.put("/api/profile", headers=H("tok-alice"), json={**PROFILE, "threshold": 140}).status_code == 422
    assert c.get("/api/me", headers=H("tok-bob")).json()["profile"] == {}
    assert store.get("profiles", "bob") is None


def test_resume_upload_review_and_delete(env):
    c, store, _ = env
    r = c.post("/api/resume", headers={**H("tok-alice"), "X-Filename": "Alice%20CV.pdf"}, content=pdf_bytes())
    assert r.status_code == 200 and r.json()["suggested"]["full_name"] == "Asha Rao"
    assert r.json()["file"]["name"] == "Alice CV.pdf"
    assert c.get("/api/resume", headers=H("tok-bob")).json()["file"] is None
    bad = c.post("/api/resume", headers={**H("tok-bob"), "X-Filename": "x.pdf"}, content=b"hello")
    assert bad.status_code == 422
    assert c.delete("/api/resume", headers=H("tok-alice")).json()["deleted"]
    assert store.get("resumes", "alice") is None


def test_gmail_connect_callback_and_disconnect(env):
    c, store, g = env
    url = c.post("/api/gmail/connect", headers=H("tok-alice")).json()["url"]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["redirect_uri"] == ["https://api.example/api/gmail/callback"] and "csecret" not in url
    r = c.get("/api/gmail/callback", params={"state": q["state"][0], "code": "abc"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://app.example/#gmail=connected"
    me = c.get("/api/me", headers=H("tok-alice")).json()
    assert me["gmail"]["connected"] and me["gmail"]["email"] == "alice@gmail.com"
    assert "rt-abc" not in json.dumps(me) and "gmail_refresh" not in json.dumps(me)
    assert c.get("/api/me", headers=H("tok-bob")).json()["gmail"]["connected"] is None
    # replaying the same state fails
    again = c.get("/api/gmail/callback", params={"state": q["state"][0], "code": "abc"}, follow_redirects=False)
    assert "gmail=error" in again.headers["location"]
    assert c.post("/api/gmail/disconnect", headers=H("tok-alice")).json() == {"connected": False}
    assert g.revoked == ["rt-abc"] and store.get("secrets", "alice") is None


def test_android_return_and_cancel(env):
    c, _, _ = env
    url = c.post("/api/gmail/connect", headers=H("tok-alice"), json={"return_to": "android"}).json()["url"]
    st = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    r = c.get("/api/gmail/callback", params={"state": st, "error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"].startswith("app.jobradar://gmail?gmail=cancelled")


def test_drafts_need_gmail_and_use_own_profile(env):
    c, store, g = env
    c.put("/api/profile", headers=H("tok-alice"), json=PROFILE)
    store.set("matches", "alice", {"jobs": [{"id": "j1", "title": "Data Engineer", "company": "Northwind", "matched_keywords": ["dbt"]}]})
    body = {"type": "application", "job_id": "j1", "to": "hr@northwind.example"}
    assert c.post("/api/drafts", headers=H("tok-alice"), json=body).status_code == 409   # not connected
    url = c.post("/api/gmail/connect", headers=H("tok-alice")).json()["url"]
    st = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    c.get("/api/gmail/callback", params={"state": st, "code": "abc"}, follow_redirects=False)
    r = c.post("/api/drafts", headers=H("tok-alice"), json=body)
    assert r.status_code == 200 and r.json()["draft"]["id"] == "d1"
    assert json.loads(store.get("users", "alice")["state"])["drafts"]["application:j1"]["id"] == "d1"
    # bob can't draft for alice's job, and has no Gmail
    assert c.post("/api/drafts", headers=H("tok-bob"), json=body).status_code == 409
    assert len(g.drafts) == 1


def test_push_subscription_validation(env):
    c, store, _ = env
    assert c.post("/api/push/subscribe", headers=H("tok-alice"), json={"subscription": {"endpoint": "http://x"}}).status_code == 422
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert c.post("/api/push/subscribe", headers=H("tok-alice"), json={"subscription": sub}).json() == {"devices": 1}
    assert store.get("push", "bob") is None


def test_cron_needs_secret(env):
    c, _, _ = env
    assert c.post("/internal/run").status_code == 403
    assert c.post("/internal/run", headers={"X-Cron-Secret": "wrong"}).status_code == 403
    r = c.post("/internal/run?refresh=false", headers={"X-Cron-Secret": "cron-s3cret"})
    assert r.status_code == 200 and "users" in r.json()


def test_delete_account(env):
    c, store, _ = env
    c.put("/api/profile", headers=H("tok-alice"), json=PROFILE)
    c.put("/api/profile", headers=H("tok-bob"), json={**PROFILE, "full_name": "Bob"})
    assert c.delete("/api/account", headers=H("tok-alice")).json()["deleted"]
    assert store.get("profiles", "alice") is None and store.get("profiles", "bob") is not None


def test_cors_only_for_the_app(env):
    c, _, _ = env
    ok = c.options("/api/me", headers={"Origin": "https://app.example", "Access-Control-Request-Method": "GET"})
    bad = c.options("/api/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://app.example"
    assert "access-control-allow-origin" not in bad.headers
