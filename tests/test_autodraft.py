"""Morning recruiter drafts: who gets one, never twice, sent/replied tracking, threaded follow-ups."""

import base64
from datetime import datetime, timedelta, timezone

import pytest

from jobradar import gmail
from jobradar.backend import autodraft, gmail_link, tasks
from jobradar.backend.store import MemoryStore

NOW = datetime(2026, 10, 26, 3, 30, tzinfo=timezone.utc)  # a Monday, 09:00 IST
PROFILE = {"full_name": "Asha Rao", "email": "asha@example.com", "current_role": "Data Engineer", "years_experience": 3,
           "skills": ["Python", "Airflow", "Snowflake"], "target_roles": ["Data Engineer"], "notice": "30 days",
           "current_ctc": "8 LPA", "expected_ctc": "12 LPA", "highlights": ["Cut pipeline costs by 30%"],
           "auto_drafts": True, "auto_draft_limit": 2, "drafts_enabled": True}


class Resp:
    def __init__(self, p, status=200): self.p, self.status_code, self.text, self.headers = p, status, "", {}
    def json(self): return self.p
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeGmail:
    """Drafts you can 'send' (draft disappears, a SENT message appears in its thread) and replies."""
    def __init__(self):
        self.drafts, self.threads, self.created, self.senders = {}, {}, [], {}

    def post(self, url, json=None, headers=None, **kw):
        assert url.endswith("/drafts")  # nothing else is ever posted: no send endpoint
        n = len(self.created) + 1
        thread = json["message"].get("threadId") or f"t{n}"
        self.created.append({**json, "_raw": base64.urlsafe_b64decode(json["message"]["raw"]).decode()})
        self.drafts[f"d{n}"] = thread
        return Resp({"id": f"d{n}", "message": {"id": f"m{n}", "threadId": thread}})

    def get(self, url, headers=None, params=None):
        if "/drafts/" in url:
            return Resp({}) if url.rsplit("/", 1)[1] in self.drafts else Resp({"error": {"code": 404, "message": "nf"}}, 404)
        if "/threads/" in url:
            tid = url.rsplit("/", 1)[1]
            return Resp({"messages": self.threads.get(tid, [])}) if tid in self.threads else Resp({"error": {"code": 404}}, 404)
        if url.endswith("/messages"):   # "from:<address>" lookups for the recruiter's name
            addr = params["q"].split("from:", 1)[1]
            return Resp({"messages": [{"id": "in-" + addr}]} if addr in self.senders else {})
        if "/messages/in-" in url:
            addr = url.split("/messages/in-", 1)[1]
            return Resp({"payload": {"headers": [{"name": "From", "value": f"{self.senders[addr]} <{addr}>"}]}})
        raise AssertionError(url)

    def send(self, draft_id, when):
        tid = self.drafts.pop(draft_id)
        self.threads.setdefault(tid, []).append(msg(["SENT"], when, "<sent-1@mail>", "Data Engineer | Python"))

    def reply(self, tid, when):
        self.threads[tid].append(msg(["INBOX"], when, "<r@x>", "Re: x"))


def msg(labels, when, mid, subject):
    return {"labelIds": labels, "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {"headers": [{"name": "Message-ID", "value": mid}, {"name": "Subject", "value": subject}]}}


def job(i, email, score=80, days_old=1, company=None):
    return {"id": f"j{i}", "title": "Data Engineer", "company": company or f"Company{i}", "location": "Hyderabad",
            "url": f"https://x/{i}", "source": "greenhouse", "score": score, "contact_emails": [email] if email else [],
            "first_seen": (NOW - timedelta(days=days_old)).isoformat(), "matched_keywords": ["Python", "Airflow"],
            "match": {"missing_skills": ["Kafka"]}}


@pytest.fixture
def env(monkeypatch):
    store, g = MemoryStore(), FakeGmail()
    store.set("profiles", "asha", dict(PROFILE))
    store.set("secrets", "asha", {"gmail_refresh": "x"})
    monkeypatch.setattr(gmail_link, "access_for", lambda st, cfg, session, uid: gmail.GmailClient(g, "t", sleep=lambda w: None))
    monkeypatch.setattr(autodraft.resume_intake, "load", lambda st, uid: ("Asha_Rao.pdf", b"%PDF-1.4 tiny"))
    return store, g


def run(store, now=NOW):
    return autodraft.run_user(store, None, None, "asha", now=now)


def drafts_of(store):
    return tasks.read_state(store, "asha")[0].get("drafts") or {}


def test_drafts_for_posted_emails_only_best_first_within_daily_limit(env):
    store, g = env
    store.set("matches", "asha", {"jobs": [
        job(1, "hr@one.example", 75), job(2, "jobs@two.example", 90), job(3, "careers@three.example", 85),
        job(4, None, 99),                                   # no email in the posting
        job(5, "noreply@five.example", 95),                 # not a person
        job(6, "hr@six.example", 95, days_old=12)]})        # too old
    r = run(store)
    assert r["created"] == 2 and r["candidates"] == 3
    recs = drafts_of(store)
    assert {d["to"] for d in recs.values()} == {"jobs@two.example", "careers@three.example"}
    assert all(d["auto"] and d["attached"] and d["type"] == "outreach" for d in recs.values())
    raw = g.created[0]["_raw"]
    assert "To: jobs@two.example" in raw and "Asha_Rao.pdf" in raw
    assert "Current CTC: 8 LPA" in raw and "Cut pipeline costs by 30%" in raw and "haven't worked with Kafka" in raw
    assert store.get("gmail", "asha")["last_auto"]["created"] == 2
    # Same day again: the limit is per day, and nothing is drafted twice
    assert run(store)["created"] == 0
    # Next day: the remaining one
    assert run(store, NOW + timedelta(days=1))["created"] == 1


def test_never_twice_to_same_address_or_excluded_company(env):
    store, _ = env
    store.update("profiles", "asha", {"excluded_companies": ["Globex"], "auto_draft_limit": 10})
    store.set("matches", "asha", {"jobs": [job(1, "hr@acme.example"), job(2, "hr@acme.example"), job(3, "t@globex.example", company="Globex Corp")]})
    assert run(store)["created"] == 1


def test_off_unless_opted_in(env):
    store, g = env
    store.update("profiles", "asha", {"auto_drafts": False})
    store.set("matches", "asha", {"jobs": [job(1, "hr@one.example")]})
    assert run(store) == {"skipped": "off"} and not g.created


def test_sent_detected_then_threaded_follow_up_after_five_working_days_then_reply_stops_it(env):
    store, g = env
    store.set("matches", "asha", {"jobs": [job(1, "hr@one.example")]})
    run(store)
    key = "outreach:j1"
    g.send(drafts_of(store)[key]["id"], NOW + timedelta(hours=2))
    thursday = NOW + timedelta(days=3)
    r = run(store, thursday)
    assert r["sent"] == 1 and r["follow_ups"] == 0
    assert drafts_of(store)[key]["sent"].startswith("2026-10-26")
    # Monday 26 → next Monday is 5 working days
    r = run(store, NOW + timedelta(days=7))
    assert r["follow_ups"] == 1
    fu = drafts_of(store)["follow_up:j1"]
    assert fu["thread_id"] == drafts_of(store)[key]["thread_id"] and fu["to"] == "hr@one.example"
    raw = g.created[-1]["_raw"]
    assert "In-Reply-To: <sent-1@mail>" in raw and "Subject: Re: Data Engineer | Python" in raw
    assert g.created[-1]["message"]["threadId"] == fu["thread_id"]
    assert run(store, NOW + timedelta(days=8))["follow_ups"] == 0   # once per job
    g.reply(fu["thread_id"], NOW + timedelta(days=8, hours=1))
    run(store, NOW + timedelta(days=9))
    assert drafts_of(store)[key]["replied"]


def test_reply_before_follow_up_means_no_follow_up(env):
    store, g = env
    store.set("matches", "asha", {"jobs": [job(1, "hr@one.example")]})
    run(store)
    rec = drafts_of(store)["outreach:j1"]
    g.send(rec["id"], NOW + timedelta(hours=1))
    g.reply(rec["thread_id"], NOW + timedelta(days=1))
    r = run(store, NOW + timedelta(days=8))
    assert r["replied"] == 1 and r["follow_ups"] == 0 and "follow_up:j1" not in drafts_of(store)


def test_deleted_draft_closes_without_follow_up(env):
    store, g = env
    store.set("matches", "asha", {"jobs": [job(1, "hr@one.example")]})
    run(store)
    g.drafts.clear()   # you deleted it in Gmail
    r = run(store, NOW + timedelta(days=1))
    assert r["deleted"] == 1 and drafts_of(store)["outreach:j1"]["closed"]
    assert run(store, NOW + timedelta(days=9))["follow_ups"] == 0


def test_working_days_skip_weekends():
    fri = datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert autodraft.working_days(fri, fri + timedelta(days=3)) == 1     # Fri → Mon
    assert autodraft.working_days(fri, fri + timedelta(days=7)) == 5     # Fri → next Fri


def test_one_users_failure_doesnt_stop_others(env, monkeypatch):
    store, _ = env
    store.set("profiles", "bob", {**PROFILE, "email": "bob@example.com"})
    store.set("secrets", "bob", {"gmail_refresh": "x"})
    store.set("matches", "asha", {"jobs": [job(1, "hr@one.example")]})
    real = gmail_link.access_for

    def access(st, cfg, session, uid):
        if uid == "bob":
            raise gmail.GmailAuthError("revoked", revoked=True)
        return real(st, cfg, session, uid)
    monkeypatch.setattr(gmail_link, "access_for", access)
    out = autodraft.run_all(store, None, None, now=NOW)
    assert out["users"] == 2 and out["created"] == 1 and out["errors"] == 1


def test_recruiter_name_from_post_then_gmail_then_address_never_guessed(env):
    store, g = env
    store.update("profiles", "asha", {"auto_draft_limit": 10})
    g.senders["rk@three.example"] = "Ravi Kumar"
    posted = job(1, "hr.desk@one.example")
    posted["contact_people"] = [{"email": "hr.desk@one.example", "name": "Priya Sharma", "source": "posting"}]
    store.set("matches", "asha", {"jobs": [posted, job(2, "anita.rao@two.example", 79), job(3, "rk@three.example", 78),
                                           job(4, "careers@four.example", 77)]})
    run(store)
    recs = {d["to"]: d for d in drafts_of(store).values()}
    assert (recs["hr.desk@one.example"]["to_name"], recs["hr.desk@one.example"]["to_name_source"]) == ("Priya Sharma", "posting")
    assert (recs["rk@three.example"]["to_name"], recs["rk@three.example"]["to_name_source"]) == ("Ravi Kumar", "gmail")
    assert (recs["anita.rao@two.example"]["to_name"], recs["anita.rao@two.example"]["to_name_source"]) == ("Anita Rao", "address")
    assert "to_name" not in recs["careers@four.example"]                      # a role address: no name made up
    raws = {c["_raw"].split("To: ", 1)[1].split("\n", 1)[0].strip(): c["_raw"] for c in g.created}
    assert "Hi Priya," in raws["Priya Sharma <hr.desk@one.example>"]
    assert "Hi," in raws["careers@four.example"] and "Hi Careers" not in raws["careers@four.example"]
