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
    with pytest.raises(drafts.DraftError, match="doesn't look valid"):
        drafts.create(client, {}, PROFILE, "application", job=JOB, to="not-an-email")


# ---------------------------------------------------- without a recruiter email
JOB2 = {**JOB, "url": "https://boards.greenhouse.io/acme/jobs/1", "apply_type": "company_site",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/1", "description_snippet": "dbt and Snowflake"}


def test_application_draft_without_recipient_lands_in_gmail_without_to():
    g = FakeGmail()
    rec = drafts.create(gmail.GmailClient(g, "tok"), {}, PROFILE, "application", job=JOB2, to="")
    m = parsed(g.drafts[0])
    assert m["To"] is None and rec["to"] == "" and "Analytics Engineer" in m["Subject"]


def test_edited_text_is_used_as_written():
    g = FakeGmail()
    drafts.create(gmail.GmailClient(g, "tok"), {}, PROFILE, "outreach", job=JOB2, to="",
                  subject="My subject", body="My own words.")
    m = parsed(g.drafts[0])
    assert m["Subject"] == "My subject" and m.get_payload().strip() == "My own words."


def test_preview_needs_no_gmail_and_no_address():
    p = drafts.preview("application", PROFILE, JOB2)
    assert p["subject"] and p["body"] and p["cover_letter"].startswith("Dear Hiring Team")
    assert p["apply"] == {"type": "company_site", "url": JOB2["apply_url"], "evidence": ""} and p["contacts"] == []
    cl = p["cover_letter"]
    assert "dbt" in cl and "Snowflake" not in cl and "30 days" in cl          # only her skills; nothing invented
    for other in ("ADF", "Databricks", "Capgemini", "Chakri"):
        assert other not in cl


def test_outreach_kit_alternatives():
    p = drafts.preview("outreach", PROFILE, {**JOB2, "contact_emails": ["hr@acme.example", "bad"]})
    assert len(p["linkedin_note"]) <= 200 and "Asha" in p["linkedin_note"]
    assert p["recruiter_search"].startswith("https://www.linkedin.com/search/results/people/")
    assert p["careers_link"] == JOB2["apply_url"]
    assert p["contacts"] == [{"email": "hr@acme.example", "source": "found in the job posting"}]  # invalid one dropped


def test_long_titles_keep_the_note_under_200():
    p = drafts.preview("outreach", PROFILE, {**JOB2, "title": "Senior Principal Analytics Engineering Lead " * 6})
    assert len(p["linkedin_note"]) <= 200 and p["linkedin_note"].endswith("Asha")


def test_follow_up_warns_when_contact_was_recent():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    state = {"gmail": {"events": [{"job_id": "j1", "at": "2026-10-22T10:00:00+00:00", "type": "acknowledged"}]}}
    p = drafts.preview("follow_up", PROFILE, JOB2, state, now)
    assert p["too_soon"] is True and "Wait" in p["advice"]
    p = drafts.preview("follow_up", PROFILE, JOB2, {}, now)
    assert p["too_soon"] is False and p["last_contact"] is None


def test_nothing_is_ever_sent():
    g = FakeGmail()
    drafts.create(gmail.GmailClient(g, "tok"), {}, PROFILE, "application", job=JOB2, to="hr@acme.example")
    assert g.sent == [] and len(g.drafts) == 1
