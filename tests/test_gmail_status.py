from datetime import datetime, timezone

import pytest

from jobradar import gmail_status as gs

JOBS = [
    {"id": "j1", "title": "Azure Data Engineer", "company": "Reveille Technologies,Inc", "url": "u1"},
    {"id": "j2", "title": "Data Engineer - Databricks", "company": "Celebal Technologies", "url": "u2"},
    {"id": "j3", "title": "Big Data Engineer", "company": "Celebal Technologies", "url": "u3"},
]
NOW = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("subject,snippet,sender,expected", [
    ("Your application was sent to Reveille Technologies,Inc", "", "LinkedIn <jobs-noreply@linkedin.com>", "applied"),
    ("Thank you for applying to Celebal", "We have received your application", "careers@celebal.com", "acknowledged"),
    ("Update on your application", "Unfortunately we have decided to move forward with other candidates", "hr@x.com", "rejected"),
    ("Interview invitation - Data Engineer", "Please join on Monday", "hr@x.com", "interview"),
    ("Next round", "Please share your availability for a call", "hr@x.com", "scheduling"),
    ("HackerRank test for Celebal", "Complete the online assessment by Friday", "noreply@hackerrank.com", "assessment"),
    ("Good news", "You have been shortlisted for the next round", "hr@x.com", "shortlisted"),
    ("Offer letter - Data Engineer", "", "hr@x.com", "offer"),
    ("Re: Data Engineer opening", "Could you share your notice period and current CTC?", "Priya <priya@acme.com>", "recruiter_reply"),
    ("30 new jobs for you: Data Engineer", "Apply now to Celebal", "LinkedIn <jobalerts-noreply@linkedin.com>", None),
    ("Data Engineer jobs matching your profile", "apply", "info@naukri.com", None),
    ("Your OTP is 1234", "", "x@y.com", None),
])
def test_classify(subject, snippet, sender, expected):
    assert gs.classify(subject, snippet, sender) == expected


def test_confidence_is_lower_when_only_the_snippet_says_so():
    assert gs.classify_ex("Unfortunately not this time", "", "hr@x.com") == ("rejected", "high")
    assert gs.classify_ex("Update", "unfortunately", "hr@x.com") == ("rejected", "medium")
    assert gs.classify_ex("Update", "", "hr@x.com", body="We regret to inform you")[1] == "medium"


def test_company_name_alone_is_not_enough():
    job, conf, cands = gs.match_ex(JOBS, "Your application was sent to Reveille Technologies,Inc", "")
    assert job["id"] == "j1" and conf == "medium"          # only the company matched: review, not apply
    assert gs.match(JOBS, "Your application was sent to Reveille Technologies,Inc", "", "") is None
    # ...unless you'd already applied to (or opened) that one job there
    applied = [dict(JOBS[0], _status="applied")] + JOBS[1:]
    assert gs.match_ex(applied, "Update from Reveille", "")[1] == "high"
    opened = [dict(JOBS[0], _pending=True)] + JOBS[1:]
    assert gs.match_ex(opened, "Update from Reveille", "")[1] == "high"


def test_title_decides_between_jobs_at_one_company():
    assert gs.match(JOBS, "Celebal: your application for Big Data Engineer", "", "")["id"] == "j3"
    job, conf, cands = gs.match_ex(JOBS, "Thanks for applying to Celebal", "")
    assert job is None and conf == "ambiguous" and {c["id"] for c in cands} == {"j2", "j3"}
    assert gs.match_ex(JOBS, "Thanks for applying to Acme", "")[1] == "none"


def test_thread_we_drafted_in_is_decisive():
    job, conf, _ = gs.match_ex(JOBS, "Re: hello", "", thread_id="t9", threads={"t9": "j2"})
    assert job["id"] == "j2" and conf == "high"


def test_extract_company():
    assert gs.extract_company("Your application was sent to Qloron Pvt Ltd") == "Qloron Pvt Ltd"
    assert gs.extract_company("Thank you for applying to Deloitte!") == "Deloitte"
    assert gs.extract_company("Hello there") is None


class Resp:
    def __init__(self, p, status=200): self.p, self.status_code, self.text = p, status, ""
    def json(self): return self.p


class FakeGmail:
    """Gmail REST: paginated list (2 per page), metadata/full messages. Records queries and full reads."""
    def __init__(self, msgs, bodies=None):
        self.msgs, self.bodies, self.queries, self.full_reads = msgs, bodies or {}, [], []

    def get(self, url, headers=None, params=None):
        assert headers["Authorization"] == "Bearer t"
        if url.endswith("/messages"):
            self.queries.append(params["q"])
            ids = [m["id"] for m in reversed(self.msgs)]  # API lists newest first
            start = int(params.get("pageToken") or 0)
            page = {"messages": [{"id": i} for i in ids[start:start + 2]]}
            if start + 2 < len(ids):
                page["nextPageToken"] = str(start + 2)
            return Resp(page)
        mid = url.rsplit("/", 1)[1]
        m = dict(next(m for m in self.msgs if m["id"] == mid))
        if params.get("format") == "full":
            self.full_reads.append(mid)
            import base64
            text = self.bodies.get(mid, "")
            m["payload"] = {**m["payload"], "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}
        return Resp(m)


def msg(mid, subject, snippet="", sender="hr@x.com", ts="2026-10-23T09:00:00+00:00", thread=None):
    ms = int(datetime.fromisoformat(ts).timestamp() * 1000)
    return {"id": mid, "threadId": thread or "t" + mid, "snippet": snippet, "internalDate": str(ms),
            "payload": {"headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}]}}


def test_run_updates_confident_matches_and_queues_the_rest():
    state = {"statuses": {"j1": {"status": "applied", "at": "2026-10-20T00:00:00+00:00"},
                          "j2": {"status": "applied", "at": "2026-10-22T00:00:00+00:00"}}, "saved": {}}
    g = FakeGmail([
        msg("m1", "Thank you for applying to Celebal Technologies", ts="2026-10-23T08:00:00+00:00"),
        msg("m2", "Your application was sent to Qloron Pvt Ltd", sender="LinkedIn <jobs-noreply@linkedin.com>",
            ts="2026-10-23T08:40:00+00:00"),
        msg("m3", "Reveille - update", "Unfortunately, we will not be moving forward", ts="2026-10-23T09:00:00+00:00"),
        msg("m4", "Your OTP", ts="2026-10-23T09:10:00+00:00"),
        msg("m5", "Interview for Data Engineer - Databricks at Celebal", ts="2026-10-23T09:20:00+00:00"),
    ])
    report = gs.run(state, JOBS, g, "t", now=NOW)
    st = state["statuses"]
    assert st["j1"]["status"] == "rejected"   # only Reveille job and you'd applied: company + your action is enough
    assert st["j2"]["status"] == "interview" and st["j2"]["source"] == "gmail"  # title matched, moved forward
    assert "j3" not in st
    added = [k for k in st if k.startswith("mail-")]
    assert len(added) == 1 and state["saved"][added[0]]["company"] == "Qloron Pvt Ltd"
    review = state["gmail"]["review"]
    assert [r["message_id"] for r in review] == ["m1"]                  # two Celebal jobs: you decide
    assert {c["id"] for c in review[0]["candidates"]} == {"j2", "j3"} and review[0]["status"] == "applied"
    assert report == {"checked": 5, "updated": 3, "added": 1, "review": 1, "replies": 0}
    assert len(set(g.queries)) == 1 and g.queries[0].startswith("newer_than:14d")  # one query, paged
    assert state["gmail"]["last_check"] == NOW.isoformat(timespec="seconds")
    # Events never carry the email text
    assert all("snippet" not in e and "body" not in e for e in state["gmail"]["events"])


def test_second_run_is_incremental_and_skips_seen_messages():
    state = {}
    g = FakeGmail([msg("m1", "Your application was sent to Qloron Pvt Ltd")])
    gs.run(state, [], g, "t", now=NOW)
    assert gs.run(state, [], g, "t", now=NOW)["checked"] == 0
    assert g.queries[1].startswith("after:")


def test_resolved_review_isnt_asked_again():
    state = {"reviews": {"r-m1": {"job_id": "j2", "at": "2026-10-23T09:30:00+00:00"}}}
    gs.run(state, JOBS, FakeGmail([msg("m1", "Thank you for applying to Celebal Technologies")]), "t", now=NOW)
    assert state["gmail"]["review"] == []


def test_body_is_read_only_when_unclear_and_never_stored():
    g = FakeGmail([msg("m1", "Update from Celebal", sender="Celebal <no-reply@greenhouse.io>"),
                   msg("m2", "Your application was sent to Qloron Pvt Ltd", sender="LinkedIn <jobs-noreply@linkedin.com>")],
                  bodies={"m1": "Unfortunately we will not be progressing your application for Big Data Engineer."})
    state = {"statuses": {"j3": {"status": "applied", "at": "2026-10-01T00:00:00+00:00"}}}
    gs.run(state, JOBS, g, "t", now=NOW)
    assert g.full_reads == ["m1"]                                   # m2 was clear from its subject
    assert state["statuses"]["j3"]["status"] == "rejected"          # title found in the body
    assert "progressing" not in repr(state)


def test_recruiter_reply_is_flagged_not_applied():
    state = {}
    g = FakeGmail([msg("m1", "Re: Azure Data Engineer", "Could you share your notice period?", sender="Priya <priya@acme.com>")])
    report = gs.run(state, JOBS, g, "t", now=NOW)
    assert report["replies"] == 1 and state["gmail"]["events"][0]["needs_reply"] and state.get("statuses") == {}


def test_run_respects_your_later_change():
    state = {"statuses": {"j1": {"status": "applied", "at": "2026-10-23T12:00:00+00:00"}}}  # you set it after the email
    gs.run(state, JOBS, FakeGmail([msg("m1", "Reveille: unfortunately no")]), "t", now=NOW)
    assert state["statuses"]["j1"]["status"] == "applied"


def test_failed_run_doesnt_record_a_successful_check():
    class Broken(FakeGmail):
        def get(self, url, headers=None, params=None):
            if not url.endswith("/messages"):
                return Resp({}, 500)
            return super().get(url, headers, params)
    state = {}
    with pytest.raises(Exception):
        gs.run(state, JOBS, Broken([msg("m1", "Offer letter")]), "t", now=NOW)
    assert "last_check" not in state.get("gmail", {})


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(gs.gmail.GmailClient, "MIN_GAP", 0)


RATE = {"error": {"code": 403, "message": "Quota exceeded for quota metric 'Total Query Cost'",
                  "errors": [{"reason": "rateLimitExceeded"}]}}


class Limited(FakeGmail):
    """Answers `allow` message reads, then says "slow down" for good."""
    def __init__(self, msgs, allow, **kw):
        super().__init__(msgs, **kw)
        self.allow = allow

    def get(self, url, headers=None, params=None):
        if not url.endswith("/messages"):
            if self.allow <= 0:
                return Resp(RATE, 403)
            self.allow -= 1
        return super().get(url, headers, params)


def quiet(state, g):
    return gs.run(state, [], gs.gmail.GmailClient(g, "t", sleep=lambda w: None), now=NOW)


def test_rate_limit_part_way_keeps_progress_and_continues_next_run():
    mails = [msg(f"m{i}", f"Your application was sent to Company{i} Pvt Ltd", ts=f"2026-10-23T0{i}:00:00+00:00")
             for i in range(1, 6)]
    state, g = {}, Limited(mails, allow=2)
    r = quiet(state, g)
    assert r["checked"] == 2 and r["partial"] and r["remaining"] == 3
    assert state["gmail"]["processed"] == ["m1", "m2"]          # oldest first, kept
    assert "last_check" not in state["gmail"]                   # window not closed: the rest are still due
    g.allow = 99
    r = quiet(state, g)
    assert r["checked"] == 3 and not r.get("partial") and state["gmail"]["last_check"]
    assert sorted(state["gmail"]["processed"]) == ["m1", "m2", "m3", "m4", "m5"]


def test_rate_limit_before_anything_is_read_is_an_error():
    state = {}
    with pytest.raises(gs.gmail.GmailApiError):
        quiet(state, Limited([msg("m1", "Offer letter")], allow=0))
    assert "last_check" not in state.get("gmail", {})


def test_unread_body_on_rate_limit_is_left_for_next_run():
    mails = [msg("m1", "Your application was sent to Qloron Pvt Ltd", ts="2026-10-23T01:00:00+00:00"),
             msg("m2", "Update", sender="no-reply@greenhouse.io", ts="2026-10-23T02:00:00+00:00")]
    state = {}
    r = quiet(state, Limited(mails, allow=2))                   # m1, m2 metadata; m2's body is refused
    assert r["checked"] == 1 and r["partial"] and state["gmail"]["processed"] == ["m1"]


def test_each_run_reads_at_most_per_run_emails():
    mails = [msg(f"m{i}", "Your OTP", ts="2026-10-23T09:00:00+00:00") for i in range(5)]
    state = {}
    r = gs.run(state, [], FakeGmail(mails), "t", now=NOW, per_run=3)
    assert r["checked"] == 3 and r["remaining"] == 2 and "last_check" not in state["gmail"]


def test_application_email_for_a_job_in_your_list_marks_it_instead_of_adding_a_copy():
    jobs = [{"id": "c1", "title": "Junior Data Engineer", "company": "Crowe Capability Center - India", "source": "linkedin"}]
    state = {}
    g = FakeGmail([msg("m1", "Your application was sent to Crowe", sender="LinkedIn <jobs-noreply@linkedin.com>")])
    r = gs.run(state, jobs, g, "t", now=NOW)
    assert r["added"] == 0 and state["statuses"]["c1"]["status"] == "applied"
    assert not [k for k in state["saved"] if k.startswith("mail-")]


def test_existing_gmail_copy_is_merged_into_the_real_job():
    mail = {"id": "mail-1", "title": "Application (from Gmail)", "company": "Crowe", "source": "email", "via": "linkedin"}
    state = {"statuses": {"mail-1": {"status": "applied", "at": "2026-10-20T10:00:00+00:00", "appliedAt": "2026-10-20T10:00:00+00:00"},
                          "c1": {"status": "new", "at": "2026-10-19T00:00:00+00:00"}},
             "saved": {"mail-1": mail}, "gmail": {"last_check": "2026-10-23T00:00:00+00:00",
                                                  "events": [{"id": "e", "job_id": "mail-1", "from": "LinkedIn"}]}}
    jobs = [{"id": "c1", "title": "Junior Data Engineer", "company": "Crowe Capabilities Center – India", "source": "linkedin"}]
    r = gs.run(state, jobs, FakeGmail([]), "t", now=NOW)
    assert r["merged"] == 1
    assert state["statuses"]["c1"]["status"] == "applied" and state["statuses"]["c1"]["appliedAt"] == "2026-10-20T10:00:00+00:00"
    assert state["statuses"]["mail-1"] == {"status": "merged", "into": "c1", "at": NOW.isoformat(timespec="seconds")}
    assert "mail-1" not in state["saved"] and state["gmail"]["events"][0]["job_id"] == "c1"


def test_company_alone_is_still_not_enough_to_merge():
    # The email came from Naukri, the only job at that company was listed on LinkedIn, and no title: keep both
    mail = {"id": "mail-1", "title": "Application (from Gmail)", "company": "Crowe", "source": "email", "via": "naukri"}
    state = {"statuses": {"mail-1": {"status": "applied", "at": "x"}}, "saved": {"mail-1": mail},
             "gmail": {"last_check": "2026-10-23T00:00:00+00:00"}}
    jobs = [{"id": "c1", "title": "Junior Data Engineer", "company": "Crowe", "source": "linkedin"},
            {"id": "c2", "title": "Data Analyst", "company": "Crowe", "source": "naukri"}]
    assert "merged" not in gs.run(state, jobs, FakeGmail([]), "t", now=NOW)
    assert "mail-1" in state["saved"]
