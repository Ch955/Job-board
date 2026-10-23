from datetime import datetime, timezone

import pytest

from jobradar import gmail_status as gs

JOBS = [
    {"id": "j1", "title": "Azure Data Engineer", "company": "Reveille Technologies,Inc", "url": "u1"},
    {"id": "j2", "title": "Data Engineer - Databricks", "company": "Celebal Technologies", "url": "u2"},
    {"id": "j3", "title": "Big Data Engineer", "company": "Celebal Technologies", "url": "u3"},
]


@pytest.mark.parametrize("subject,snippet,sender,expected", [
    ("Your application was sent to Reveille Technologies,Inc", "", "LinkedIn <jobs-noreply@linkedin.com>", "applied"),
    ("Thank you for applying to Celebal", "We have received your application", "careers@celebal.com", "applied"),
    ("Update on your application", "Unfortunately we have decided to move forward with other candidates", "hr@x.com", "rejected"),
    ("Interview invitation - Data Engineer", "Please pick a slot", "hr@x.com", "interview"),
    ("HackerRank test for Celebal", "Complete the online assessment by Friday", "noreply@hackerrank.com", "interview"),
    ("30 new jobs for you: Data Engineer", "Apply now to Celebal", "LinkedIn <jobalerts-noreply@linkedin.com>", None),
    ("Data Engineer jobs matching your profile", "apply", "info@naukri.com", None),
    ("Your OTP is 1234", "", "x@y.com", None),
])
def test_classify(subject, snippet, sender, expected):
    assert gs.classify(subject, snippet, sender) == expected


def test_match_uses_company_then_title():
    assert gs.match(JOBS, "Your application was sent to Reveille Technologies,Inc", "", "")["id"] == "j1"
    assert gs.match(JOBS, "Celebal: your application for Big Data Engineer", "", "")["id"] == "j3"
    assert gs.match(JOBS, "Thanks for applying to Celebal", "", "") is None  # two Celebal jobs, no clue
    assert gs.match(JOBS, "Thanks for applying to Acme", "", "") is None


def test_extract_company():
    assert gs.extract_company("Your application was sent to Qloron Pvt Ltd") == "Qloron Pvt Ltd"
    assert gs.extract_company("Thank you for applying to Deloitte!") == "Deloitte"
    assert gs.extract_company("Hello there") is None


class Resp:
    def __init__(self, p): self.p = p
    def json(self): return self.p
    def raise_for_status(self): pass


class FakeGmail:
    def __init__(self, msgs): self.msgs = msgs
    def get(self, url, headers=None, params=None):
        assert headers["Authorization"] == "Bearer t"
        if url.endswith("/messages"):
            assert "newer_than:3d" in params["q"]
            return Resp({"messages": [{"id": m["id"]} for m in reversed(self.msgs)]})  # API lists newest first
        mid = url.rsplit("/", 1)[1]
        return Resp(next(m for m in self.msgs if m["id"] == mid))


def msg(mid, subject, snippet="", sender="hr@x.com", ts="2026-10-23T09:00:00+00:00"):
    ms = int(datetime.fromisoformat(ts).timestamp() * 1000)
    return {"id": mid, "threadId": "t" + mid, "snippet": snippet, "internalDate": str(ms),
            "payload": {"headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}]}}


def test_run_updates_statuses_forward_only():
    state = {"statuses": {"j2": {"status": "interview", "at": "2026-10-22T00:00:00+00:00"}}, "saved": {}}
    g = FakeGmail([
        msg("m1", "Your application was sent to Reveille Technologies,Inc", ts="2026-10-23T08:00:00+00:00"),
        msg("m2", "Celebal | Data Engineer - Databricks", "Thank you for your application", ts="2026-10-23T08:30:00+00:00"),
        msg("m3", "Your application was sent to Qloron Pvt Ltd", ts="2026-10-23T08:40:00+00:00"),
        msg("m4", "Reveille - update", "Unfortunately, we will not be moving forward", ts="2026-10-23T09:00:00+00:00"),
    ])
    report = gs.run(state, JOBS, g, "t", now=datetime(2026, 10, 23, 10, tzinfo=timezone.utc))
    st = state["statuses"]
    assert st["j1"]["status"] == "rejected" and st["j1"]["appliedAt"] == "2026-10-23T08:00:00+00:00"
    assert st["j2"]["status"] == "interview"                      # "applied" email doesn't move it back
    added = [k for k in st if k.startswith("mail-")]
    assert len(added) == 1 and state["saved"][added[0]]["company"] == "Qloron Pvt Ltd"
    assert report == {"checked": 4, "updated": 3, "added": 1}
    assert state["gmail"]["events"][0]["status"] == "rejected"
    # Second run: nothing new to read
    assert gs.run(state, JOBS, g, "t")["checked"] == 0


def test_run_respects_your_later_change():
    state = {"statuses": {"j1": {"status": "new", "at": "2026-10-23T12:00:00+00:00"}}}  # you undid it after the email
    gs.run(state, JOBS, FakeGmail([msg("m1", "Your application was sent to Reveille Technologies,Inc")]), "t")
    assert state["statuses"]["j1"]["status"] == "new"
