"""Per-user drafts: only the user's own facts, right thread, right resume, never twice, never sent."""

import base64
import email

import pytest

from jobradar import gmail
from jobradar.backend import drafts

PROFILE = {"full_name": "Asha Rao", "email": "asha@example.com", "current_role": "Data Engineer",
           "years_experience": 3, "skills": ["Spark", "Airflow", "dbt", "SQL"], "notice": "30 days",
           "location": "Pune", "linkedin": "linkedin.com/in/asha"}
JOB = {"id": "j1", "title": "Analytics Engineer", "company": "Acme", "matched_keywords": ["dbt", "Snowflake", "SQL"]}


class Resp:
    def __init__(self, p, status=200): self.p, self.status_code, self.text = p, status, ""
    def json(self): return self.p
    def raise_for_status(self): assert self.status_code < 400


class FakeGmail:
    def __init__(self): self.drafts, self.sent = [], []
    def post(self, url, headers=None, json=None, **kw):
        assert url.endswith("/drafts"), "only drafts are ever created"
        self.drafts.append(json["message"])
        return Resp({"id": f"d{len(self.drafts)}", "message": {"id": "x", "threadId": json["message"].get("threadId", "new")}})
    def get(self, url, headers=None, params=None):
        return Resp({"id": "m9", "threadId": "t9", "payload": {"headers": [
            {"name": "From", "value": "Priya Shah <priya@acme.com>"}, {"name": "Subject", "value": "Data role at Acme"},
            {"name": "Message-ID", "value": "<abc@acme.com>"}]}})


def parsed(message):
    return email.message_from_bytes(base64.urlsafe_b64decode(message["raw"]))


def test_application_uses_only_the_users_facts():
    subject, body = drafts.compose("application", PROFILE, JOB)
    assert "Asha Rao" in subject and "Analytics Engineer" in subject
    assert "dbt" in body and "Snowflake" not in body          # Snowflake is in the job, not in her skills
    assert "3 years" in body and "30 days" in body
    for other in ("ADF", "Databricks", "PySpark", "Capgemini", "Chakri"):
        assert other not in body


def test_missing_facts_are_left_out_not_invented():
    p = {"full_name": "Ben", "email": "ben@example.com"}
    _, body = drafts.compose("application", p, {"id": "j", "title": "Data Engineer", "company": "X"})
    assert "years" not in body and "Notice" not in body and "hands-on" not in body
    with pytest.raises(drafts.DraftError):
        drafts.compose("application", {"email": "x@y.com"}, JOB)


@pytest.mark.parametrize("kind", sorted(drafts.TYPES))
def test_every_type_renders(kind):
    subject, body = drafts.compose(kind, PROFILE, JOB, {"subject": "Data role", "from_name": "Priya Shah"})
    assert subject and body.strip().endswith("linkedin.com/in/asha")


def test_create_attaches_this_users_resume_and_dedupes():
    g, state = FakeGmail(), {}
    client = gmail.GmailClient(g, "tok")
    rec = drafts.create(client, state, PROFILE, "application", job=JOB, to="hr@acme.com", resume=("Asha_Rao.docx", b"PK.."))
    m = parsed(g.drafts[0])
    att = [p for p in m.walk() if p.get_filename()]
    assert att[0].get_filename() == "Asha_Rao.docx" and "wordprocessingml" in att[0].get_content_type()
    assert rec["attached"] and state["drafts"]["application:j1"]["id"] == "d1"
    again = drafts.create(client, state, PROFILE, "application", job=JOB, to="hr@acme.com", resume=("Asha_Rao.docx", b"PK.."))
    assert again["duplicate"] and len(g.drafts) == 1


def test_without_resume_the_draft_doesnt_claim_one():
    g = FakeGmail()
    drafts.create(gmail.GmailClient(g, "tok"), {}, PROFILE, "application", job=JOB, to="hr@acme.com")
    assert "attached" not in parsed(g.drafts[0]).get_payload()


def test_reply_goes_into_the_recruiters_thread():
    g, state = FakeGmail(), {}
    rec = drafts.create(gmail.GmailClient(g, "tok"), state, PROFILE, "reply", job=JOB, reply_to_message="m9")
    m = parsed(g.drafts[0])
    assert g.drafts[0]["threadId"] == "t9" and m["In-Reply-To"] == "<abc@acme.com>" and m["To"] == "priya@acme.com"
    assert m["Subject"] == "Re: Data role at Acme" and "Hi Priya," in m.get_payload()
    assert "[fill in]" in m.get_payload() and not [p for p in m.walk() if p.get_filename()]
    assert "reply:t9" in state["drafts"] and rec["type"] == "reply"


def test_disabled_or_no_address():
    client = gmail.GmailClient(FakeGmail(), "tok")
    with pytest.raises(drafts.DraftError, match="turned off"):
        drafts.create(client, {}, {**PROFILE, "drafts_enabled": False}, "application", job=JOB, to="hr@acme.com")
    with pytest.raises(drafts.DraftError, match="email address"):
        drafts.create(client, {}, PROFILE, "application", job=JOB, to="")
