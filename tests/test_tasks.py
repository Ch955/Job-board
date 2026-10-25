"""Scheduled per-user work: shared pool, per-user matches, isolated Gmail sync, account deletion."""

import json
from datetime import datetime, timezone

import pytest

from jobradar import gmail
from jobradar.backend import gmail_link, profile, resume_intake, secretbox, tasks
from jobradar.backend.store import MemoryStore
from jobradar.config import load_config
from jobradar.models import Job
from tests.resume_fixtures import pdf_bytes

NOW = datetime(2026, 10, 23, 6, tzinfo=timezone.utc)
CFG = gmail_link.OAuthConfig("cid", "csecret", "https://api.example/api/gmail/callback")
BASE = load_config()


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", secretbox.new_key())


def J(title, company, desc, location="Pune, India"):
    return Job(title=title, company=company, location=location, url=f"https://x/{company}", source="greenhouse",
               posted_date="2026-10-22", description=desc)


POSTINGS = [
    J("Data Engineer", "Northwind", "Airflow dbt Snowflake PySpark Python SQL, 2-4 years experience"),
    J("Frontend Developer", "Contoso", "React TypeScript JavaScript, 1-3 years experience"),
    J("Accountant", "Fabrikam", "Tally GST"),
    J("Data Engineer", "Abroad Inc", "Airflow dbt", location="Berlin, Germany"),
]


def fake_fetch(cfg, session, state, now=None, **kw):
    fake_fetch.cfg = cfg
    return [Job(**{**j.__dict__}) for j in POSTINGS], {"greenhouse": {"status": "ok", "count": len(POSTINGS)}}


def users(store):
    store.set("profiles", "alice", profile.clean({"full_name": "Alice", "email": "a@example.com", "target_roles": ["Data Engineer"],
              "years_experience": 3, "skills": ["Airflow", "dbt", "Snowflake", "PySpark", "Python", "SQL"], "locations": ["Pune"]}))
    store.set("profiles", "bob", profile.clean({"full_name": "Bob", "email": "b@example.com", "target_roles": ["Frontend Developer"],
              "years_experience": 2, "skills": ["React", "TypeScript", "JavaScript"], "anywhere_in_india": True, "threshold": 60}))
    store.set("profiles", "new", profile.clean({"full_name": "New"}))  # hasn't finished onboarding


def test_pool_is_shared_and_matches_are_per_user():
    store = MemoryStore()
    users(store)
    r = tasks.run_all(store, BASE, session=None, now=NOW, fetcher=fake_fetch)
    assert set(fake_fetch.cfg["sources"]["jobspy"]["queries"]) == {"Data Engineer", "Frontend Developer"}
    assert r["pool"]["jobs"] == 2 and r["users"] == 2 and r["matched"] == 2     # accountant + abroad dropped
    a = store.get("matches", "alice")["jobs"]
    b = store.get("matches", "bob")["jobs"]
    assert [j["company"] for j in a] == ["Northwind"] and [j["company"] for j in b] == ["Contoso"]
    assert "description" not in a[0] and a[0]["match"]["matched_skills"]
    assert store.get("matches", "new") is None


def test_pool_survives_a_source_outage():
    store = MemoryStore()
    users(store)
    tasks.refresh_pool(store, BASE, None, NOW, fake_fetch)
    tasks.refresh_pool(store, BASE, None, NOW, lambda *a, **k: ([], {"greenhouse": {"status": "error"}}))
    assert len(tasks.load_pool(store)) == 2


class Resp:
    def __init__(self, p, status=200): self.p, self.status_code, self.text = p, status, ""
    def json(self): return self.p
    def raise_for_status(self): pass


class Google:
    """Token endpoint + Gmail, where each user's token only sees that user's mailbox."""
    def __init__(self, boxes, dead=()):
        self.boxes, self.dead = boxes, set(dead)

    def post(self, url, data=None, **kw):
        if url == gmail.REVOKE_URL:
            return Resp({})
        rt = data["refresh_token"]
        if rt in self.dead:
            return Resp({"error": "invalid_grant"}, 400)
        return Resp({"access_token": "at-" + rt})

    def get(self, url, headers=None, params=None):
        box = self.boxes[headers["Authorization"].split("at-")[1]]
        if url.endswith("/messages"):
            return Resp({"messages": [{"id": m["id"]} for m in box]})
        mid = url.rsplit("/", 1)[1]
        return Resp(next(m for m in box if m["id"] == mid))


def mail(mid, subject):
    return {"id": mid, "threadId": "t" + mid, "snippet": "", "internalDate": str(int(NOW.timestamp() * 1000) - 3600_000),
            "payload": {"headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": "HR <hr@x.com>"}]}}


def connect(store, uid, refresh):
    store.set("secrets", uid, {"gmail_refresh": secretbox.seal(refresh.encode(), uid=uid, purpose="gmail")})
    store.update("gmail", uid, {"connected": True})


def test_gmail_sync_is_isolated_per_user():
    store = MemoryStore()
    users(store)
    tasks.run_all(store, BASE, None, now=NOW, fetcher=fake_fetch)
    store.set("users", "alice", {"state": json.dumps({"statuses": {}})})
    connect(store, "alice", "rt-a")
    connect(store, "bob", "rt-b")
    g = Google({"rt-a": [mail("a1", "Interview for Data Engineer at Northwind")],
                "rt-b": [mail("b1", "Offer letter: Frontend Developer at Contoso")]}, dead={"rt-b-not"})
    r = tasks.run_all(store, BASE, g, oauth=CFG, now=NOW, refresh=False)
    assert r["gmail"] == 2
    sa = json.loads(store.get("users", "alice")["state"])
    sb = json.loads(store.get("users", "bob")["state"])
    assert list(sa["statuses"].values())[0]["status"] == "interview" and "a1" in sa["gmail"]["processed"]
    assert list(sb["statuses"].values())[0]["status"] == "offer" and "a1" not in sb["gmail"]["processed"]
    assert store.get("gmail", "alice")["last_success"] == NOW.isoformat(timespec="seconds")


def test_one_users_revoked_gmail_doesnt_affect_another():
    store = MemoryStore()
    connect(store, "alice", "rt-a")
    connect(store, "bob", "rt-dead")
    g = Google({"rt-a": [mail("a1", "Your application was sent to Qloron")]}, dead={"rt-dead"})
    store.update("gmail", "bob", {"last_success": "2026-10-20T00:00:00+00:00"})
    tasks.gmail_sync_user(store, CFG, g, "bob", NOW)
    assert tasks.gmail_sync_user(store, CFG, g, "alice", NOW).get("checked") == 1
    bob = store.get("gmail", "bob")
    assert bob["connected"] is False and "Connect Gmail again" in bob["last_error"]
    assert bob["last_success"] == "2026-10-20T00:00:00+00:00"            # a failure never looks like a success
    assert store.get("secrets", "bob") is None and store.get("secrets", "alice")


def test_delete_user_removes_everything():
    store = MemoryStore()
    users(store)
    tasks.run_all(store, BASE, None, now=NOW, fetcher=fake_fetch)
    connect(store, "alice", "rt-a")
    resume_intake.save(store, "alice", "cv.pdf", pdf_bytes(), NOW)
    store.set("push", "alice", {"subscriptions": [{"endpoint": "e"}]})
    store.set("users", "alice", {"state": "{}"})
    gmail_link.start(store, CFG, "alice")
    deleted = []
    tasks.delete_user(store, Google({}), "alice", deleted.append)
    left = [k for k in store.docs if "alice" in k[1] or (store.docs[k][0].get("uid") == "alice")]
    assert left == [] and deleted == ["alice"]
    assert store.get("matches", "bob") is not None                       # nobody else touched


class GmailSays(Google):
    """Token works; the Gmail API answers with a given error."""
    def __init__(self, status, body):
        super().__init__({})
        self.status, self.body = status, body
    def get(self, url, headers=None, params=None):
        return Resp(self.body, self.status)


DISABLED = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Gmail API has not been used in project 1 before or it is disabled.",
                      "details": [{"reason": "SERVICE_DISABLED"}]}}
SCOPE = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes.",
                   "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}


def test_gmail_api_not_enabled_is_explained(caplog):
    store = MemoryStore()
    connect(store, "alice", "rt-a")
    r = tasks.gmail_sync_user(store, CFG, GmailSays(403, DISABLED), "alice", NOW)
    g = store.get("gmail", "alice")
    assert r == {"error": "gmail 403"} and "Gmail API isn't enabled" in g["last_error"]
    assert store.get("secrets", "alice")                       # not disconnected: nothing wrong with the user's access
    assert "SERVICE_DISABLED" in caplog.text and "alice" not in caplog.text and "rt-a" not in caplog.text


def test_missing_permission_tells_the_user_to_reconnect():
    store = MemoryStore()
    connect(store, "alice", "rt-a")
    tasks.gmail_sync_user(store, CFG, GmailSays(403, SCOPE), "alice", NOW)
    assert "connect again and tick both boxes" in store.get("gmail", "alice")["last_error"]


def test_unexpected_errors_log_a_traceback_without_secrets(caplog, monkeypatch):
    store = MemoryStore()
    connect(store, "alice", "rt-a")
    monkeypatch.setattr(tasks.gmail_status, "run", lambda *a, **k: 1 / 0)
    r = tasks.gmail_sync_user(store, CFG, Google({"rt-a": []}), "alice", NOW)
    assert r == {"error": "ZeroDivisionError"} and "ZeroDivisionError" in store.get("gmail", "alice")["last_error"]
    assert "Traceback" in caplog.text and "rt-a" not in caplog.text
