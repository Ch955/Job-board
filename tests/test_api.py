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
    quiet = api.PoolSearch(store, load_config(), fetcher=lambda *a, **k: ([], {}), session_factory=lambda: None, background=False)
    app = api.create_app(settings, store, g, FakeVerifier(), load_config(), search=quiet)
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


# ------------------------------------------------------------------ job search on demand
from datetime import datetime, timedelta, timezone  # noqa: E402

from jobradar.backend import tasks  # noqa: E402
from jobradar.models import Job  # noqa: E402


def make_search_app(monkeypatch, jobs):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", secretbox.new_key())
    calls = []

    def fetcher(cfg, session, state, now=None, **kw):
        calls.append(cfg["sources"]["jobspy"]["queries"])
        return [Job(**j.__dict__) for j in jobs], {"greenhouse": {"status": "ok"}}

    store = MemoryStore()
    search = api.PoolSearch(store, load_config(), fetcher=fetcher, session_factory=lambda: None, background=False)
    settings = api.Settings(project_id="p", allowed_origins=[])
    app = api.create_app(settings, store, Google(), FakeVerifier(), load_config(), search=search)
    return TestClient(app), store, search, calls


def J(title, desc):
    return Job(title=title, company="Northwind", location="Pune, India", url="https://x/1", source="greenhouse",
               posted_date=datetime.now(timezone.utc).date().isoformat(), description=desc)


DE = {**PROFILE, "target_roles": ["Data Engineer"], "skills": ["Airflow", "dbt", "Python", "SQL"], "threshold": 50,
      "locations": ["Pune"]}


def test_saving_a_profile_searches_when_the_pool_is_empty(monkeypatch):
    c, store, search, calls = make_search_app(monkeypatch, [J("Data Engineer", "Airflow dbt Python SQL, 2-4 years experience")])
    r = c.put("/api/profile", headers=H("tok-alice"), json=DE).json()
    assert r["searching"] is True and calls == [["Data Engineer"]]          # searched for her role, right away
    m = c.get("/api/matches", headers=H("tok-alice")).json()
    assert [j["title"] for j in m["jobs"]] == ["Data Engineer"] and m["searching"] is False and m["pool_count"] == 1


def test_no_new_search_while_the_pool_is_fresh_and_covers_your_roles(monkeypatch):
    c, store, search, calls = make_search_app(monkeypatch, [J("Data Engineer", "Airflow dbt")])
    c.put("/api/profile", headers=H("tok-alice"), json=DE)
    c.post("/api/matches/refresh", headers=H("tok-alice"))
    assert len(calls) == 1
    # a new role nobody searched for yet triggers one at once
    c.put("/api/profile", headers=H("tok-alice"), json={**DE, "target_roles": ["Data Engineer", "Analytics Engineer"]})
    assert len(calls) == 2 and "Analytics Engineer" in calls[1]


def test_refresh_after_an_hour_searches_again(monkeypatch):
    c, store, search, calls = make_search_app(monkeypatch, [J("Data Engineer", "Airflow dbt")])
    c.put("/api/profile", headers=H("tok-alice"), json=DE)
    meta = store.get("pool", "meta")
    meta["updated"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    store.set("pool", "meta", meta)
    search.last_started -= search.MIN_GAP + 1
    r = c.post("/api/matches/refresh", headers=H("tok-alice")).json()
    assert len(calls) == 2 and r["count"] == 1


def test_pool_status_reasons():
    store = MemoryStore()
    assert tasks.pool_status(store) == "empty"
    now = datetime.now(timezone.utc)
    store.set("pool", "meta", {"count": 3, "updated": now.isoformat(), "roles": ["Data Engineer"]})
    assert tasks.pool_status(store, {"target_roles": ["data engineer"]}, now) is None
    assert tasks.pool_status(store, {"target_roles": ["QA Engineer"]}, now) == "new roles"
    assert tasks.pool_status(store, {"target_roles": ["Data Engineer"]}, now + timedelta(hours=2)) == "stale"


def test_a_failed_search_is_reported_and_doesnt_stick(monkeypatch):
    c, store, search, calls = make_search_app(monkeypatch, [])
    search.fetcher = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network"))
    r = c.put("/api/profile", headers=H("tok-alice"), json=DE).json()
    m = c.get("/api/matches", headers=H("tok-alice")).json()
    assert r["searching"] is True and m["searching"] is False and m["search_error"] == "RuntimeError"


def test_health_shows_which_sites_supplied_jobs(monkeypatch):
    c, store, search, calls = make_search_app(monkeypatch, [J("Data Engineer", "Airflow dbt")])
    c.put("/api/profile", headers=H("tok-alice"), json=DE)
    h = c.get("/healthz").json()
    assert h["jobs"]["in_list"] == 1 and h["jobs"]["by_site"] == {"greenhouse": 1}
    assert h["jobs"]["last_search"]["greenhouse"]["status"] == "ok" and "alice" not in str(h)


def test_draft_preview_without_gmail_or_recipient(env):
    c, store, g = env
    c.put("/api/profile", headers=H("tok-alice"), json=PROFILE)
    store.set("matches", "alice", {"jobs": [{"id": "j1", "title": "Data Engineer", "company": "Northwind",
                                             "url": "https://boards.greenhouse.io/nw/1", "apply_type": "company_site",
                                             "matched_keywords": ["dbt"]}]})
    p = c.post("/api/drafts/preview", headers=H("tok-alice"), json={"type": "application", "job_id": "j1"}).json()
    assert p["cover_letter"] and p["apply"]["type"] == "company_site" and g.drafts == []
    o = c.post("/api/drafts/preview", headers=H("tok-alice"), json={"type": "outreach", "job_id": "j1"}).json()
    assert len(o["linkedin_note"]) <= 200 and o["recruiter_search"]
    assert c.post("/api/drafts/preview", headers=H("tok-bob"), json={"type": "application", "job_id": "j1"}).status_code == 404
    assert c.post("/api/drafts/preview", json={"type": "application", "job_id": "j1"}).status_code == 401


def test_gmail_draft_without_recipient_and_with_edits(env):
    c, store, g = env
    c.put("/api/profile", headers=H("tok-alice"), json=PROFILE)
    store.set("matches", "alice", {"jobs": [{"id": "j1", "title": "Data Engineer", "company": "Northwind"}]})
    url = c.post("/api/gmail/connect", headers=H("tok-alice")).json()["url"]
    st = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    c.get("/api/gmail/callback", params={"state": st, "code": "abc"}, follow_redirects=False)
    bad = c.post("/api/drafts", headers=H("tok-alice"), json={"type": "application", "job_id": "j1", "to": "nobody"})
    assert bad.status_code == 422 and "valid" in bad.json()["detail"]
    import time as _t
    _t.sleep(3.1)
    r = c.post("/api/drafts", headers=H("tok-alice"), json={"type": "application", "job_id": "j1", "to": "",
                                                             "subject": "Edited", "body": "Edited body"})
    assert r.status_code == 200 and r.json()["draft"]["to"] == ""
    import base64
    raw = base64.urlsafe_b64decode(g.drafts[-1]["message"]["raw"]).decode()
    assert "Subject: Edited" in raw and "Edited body" in raw and "\nTo:" not in raw


def test_walkins_endpoint_uses_your_own_walkin_cities(env):
    c, store, g = env
    full = ("Walk-in drive. Date: 30th December 2099. Time: 10 AM - 1 PM. Venue: Tower A, Hitec City, Hyderabad. "
            "Experience: 2-5 years. SQL, Airflow, dbt.")
    pool = [{"id": "w1", "title": "Walk-in: Data Engineer", "company": "Northwind", "location": "Hyderabad, India",
             "url": "https://boards.greenhouse.io/nw/1", "source": "greenhouse", "posted_date": None, "description": full}]
    store.set("pool", "c0", {"jobs": pool})
    store.set("pool", "meta", {"chunks": 1, "count": 1, "updated": "2099-01-01T00:00:00+00:00"})
    c.put("/api/profile", headers=H("tok-alice"), json={**PROFILE, "walkin_cities": ["Hyderabad"], "walkin_min_score": 0})
    c.put("/api/profile", headers=H("tok-bob"), json={**PROFILE, "walkin_cities": ["Chennai"], "walkin_min_score": 0})
    a = c.get("/api/walkins", headers=H("tok-alice")).json()
    b = c.get("/api/walkins", headers=H("tok-bob")).json()
    assert [e["company"] for e in a["upcoming"]] == ["Northwind"] and a["upcoming"][0]["status"] == "confirmed"
    assert b["upcoming"] == [] and b["cities"] == ["Chennai"]
    assert c.get("/api/walkins").status_code == 401


def test_skills_add_hide_remove_and_matches_carry_scan_and_run_info(env):
    c, store, _ = env
    assert c.put("/api/profile", headers=H("tok-alice"), json=PROFILE).status_code == 200
    store.set("pool", "meta", {"updated": "2026-10-25T06:00:00+00:00", "sources": {
        "greenhouse": {"status": "ok", "count": 9, "warnings": ["greenhouse/acme: HTTP 404"]},
        "remotive": {"status": "error", "error": "ConnectionError: boom"},
        "jobspy": {"status": "ok", "count": 5, "warnings": ["glassdoor 'Data Engineer': HTTPError: 403 Forbidden"]}}})
    r = c.post("/api/skills", headers=H("tok-alice"), json={"action": "add", "skill": "pyspark"}).json()
    assert "PySpark" in r["profile"]["skills"] and r["profile"]["added_skills"] == ["PySpark"]
    r = c.post("/api/skills", headers=H("tok-alice"), json={"action": "hide", "skill": "Kafka"}).json()
    assert r["profile"]["hidden_skills"] == ["Kafka"]
    r = c.post("/api/skills", headers=H("tok-alice"), json={"action": "remove", "skill": "PySpark"}).json()
    assert "PySpark" not in r["profile"]["skills"] and r["profile"]["added_skills"] == []
    assert c.post("/api/skills", headers=H("tok-alice"), json={"action": "delete-all", "skill": "x"}).status_code == 422
    assert c.post("/api/skills", headers=H("tok-bob"), json={"action": "add", "skill": "x"}).status_code in (200, 422)
    assert "PySpark" not in (store.get("profiles", "bob") or {}).get("skills", [])  # bob's call never touches alice
    m = c.get("/api/matches", headers=H("tok-alice")).json()
    assert m["run"]["at"] == "2026-10-25T06:00:00+00:00"
    assert {f["source"] for f in m["run"]["failed"]} == {"remotive", "glassdoor"}   # one company's 404 isn't a failure
    assert m["skills_scan"]["days"] == 30
