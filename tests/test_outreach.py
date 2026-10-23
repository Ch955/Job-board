import base64
import email
import json
from datetime import datetime, timedelta, timezone

import pytest

from jobradar import gmail, outreach, resume
from jobradar.models import Job

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def job(title="Azure Data Engineer", company="Acme Analytics", desc="", score=85, status="new", emails=None):
    j = Job(title=title, company=company, location="Hyderabad, India", url="https://x/1", source="naukri",
            description=desc, score=score, status=status, matched_keywords=["ADF", "Databricks", "PySpark", "SQL"],
            score_breakdown={"location_label": "Hyderabad"})
    j.contact_emails = emails if emails is not None else outreach.extract_emails(desc)
    return j


def test_extract_emails_keeps_people_drops_noise():
    text = ("Share your CV with Priya.K@acme-analytics.com or hr@acme.in. Questions: noreply@acme.com, "
            "privacy@acme.com, support@naukri.com, priya.k@acme-analytics.com.")
    assert outreach.extract_emails(text) == ["priya.k@acme-analytics.com", "hr@acme.in"]


def test_email_and_note_render(config):
    subject, body = outreach.email_for(job(), config)
    assert subject == "Application: Azure Data Engineer | Azure Data Engineer, 3+ yrs | Immediate joiner"
    assert "Acme Analytics (Hyderabad)" in body and "ADF, Databricks, PySpark" in body
    assert "I'm an immediate joiner" in body and "{" not in body
    note = outreach.linkedin_note(job(title="Senior Azure Data Engineer - Databricks, ADF, Synapse, Purview"), config)
    assert len(note) <= 200 and note.startswith("Hi! I'm an Azure Data Engineer") and note.endswith("Thanks, Chakri")


def test_signature_secret_appended(config, monkeypatch):
    monkeypatch.setenv("EMAIL_SIGNATURE", "Phone: +91-0000000000")
    assert outreach.email_for(job(), config)[1].endswith("Phone: +91-0000000000")


def test_resume_roundtrip(tmp_path, monkeypatch):
    pdf = tmp_path / "Chakri_Resume.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake resume bytes")
    out = tmp_path / "resume.pdf.enc"
    key = resume.encrypt_file(pdf, out)
    assert b"fake resume" not in out.read_bytes()
    assert resume.decrypt_file(key, out) == ("Chakri_Resume.pdf", b"%PDF-1.7 fake resume bytes")
    with pytest.raises(Exception):
        resume.decrypt_file(resume.encrypt_file(pdf, tmp_path / "other.enc"), out)  # wrong key


def test_build_message_with_attachment():
    msg = gmail.build_message("hr@acme.in", "Hello", "Body text", "me@gmail.com", ("cv.pdf", b"%PDF-1"))
    parsed = email.message_from_bytes(msg.as_bytes())
    assert parsed["To"] == "hr@acme.in" and parsed["From"] == "me@gmail.com"
    parts = list(parsed.walk())
    assert any(p.get_filename() == "cv.pdf" and p.get_payload(decode=True) == b"%PDF-1" for p in parts)


class Resp:
    def __init__(self, code, payload):
        self.status_code, self._p = code, payload
    def json(self):
        return self._p
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeGoogle:
    def __init__(self, token_code=200):
        self.token_code, self.drafts = token_code, []
    def post(self, url, data=None, json=None, headers=None):
        if url == gmail.TOKEN_URL:
            return Resp(self.token_code, {"access_token": "at"} if self.token_code == 200 else {"error": "invalid_grant"})
        assert headers["Authorization"] == "Bearer at"
        raw = base64.urlsafe_b64decode(json["message"]["raw"])
        self.drafts.append(email.message_from_bytes(raw))
        n = len(self.drafts)
        return Resp(200, {"id": f"r-{n}", "message": {"id": f"18f{n}"}})


@pytest.fixture
def gmail_env(monkeypatch):
    for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("RESUME_KEY", raising=False)


def test_create_drafts_rules(config, gmail_env):
    jobs = [job(emails=["a@acme.in"]), job(company="Beta", emails=["b@beta.in"], score=50),
            job(company="Gamma", emails=[]), job(company="Delta", emails=["d@delta.in"], status="applied"),
            job(company="Acme again", emails=["a@acme.in"])]
    state, g = {}, FakeGoogle()
    report = outreach.create_drafts(jobs, state, config, g, NOW)
    assert report["created"] == 1 and len(g.drafts) == 1            # weak, no-email, applied, same-address skipped
    assert g.drafts[0]["To"] == "a@acme.in"
    assert jobs[0].outreach["draft_id"] == "r-1" and jobs[0].outreach["message_id"] == "18f1"
    assert state["emailed"] == {"a@acme.in": NOW.isoformat(timespec="seconds")}
    # Next run: nothing new to draft
    assert outreach.create_drafts(jobs, state, config, g, NOW + timedelta(hours=3))["created"] == 0
    # After the cooldown the same address can be drafted again for a different job
    later = NOW + timedelta(days=31)
    assert outreach.create_drafts(jobs, state, config, g, later)["created"] == 1


def test_create_drafts_unconfigured_or_bad_token(config, gmail_env, monkeypatch):
    jobs = [job(emails=["a@acme.in"])]
    r = outreach.create_drafts(jobs, {}, config, FakeGoogle(token_code=400), NOW)
    assert "invalid_grant" in r["skipped"] and "gmail-auth" in r["skipped"] and not jobs[0].outreach
    monkeypatch.delenv("GMAIL_REFRESH_TOKEN")
    assert outreach.create_drafts(jobs, {}, config, FakeGoogle(), NOW)["skipped"] == "GMAIL_REFRESH_TOKEN not set"


def test_pipeline_keeps_outreach_and_publishes_templates(config, tmp_path):
    from jobradar import pipeline
    from jobradar.offline import OfflineSession, enable_fake_keys
    enable_fake_keys()
    out = tmp_path / "jobs.json"
    pipeline.run(config, session=OfflineSession(), output=out, ignore_throttle=True)
    data = json.loads(out.read_text())
    data["jobs"][0]["outreach"] = {"draft_id": "r-9", "email": "x@y.in"}
    out.write_text(json.dumps(data))
    pipeline.run(config, session=OfflineSession(), output=out, ignore_throttle=True)
    again = json.loads(out.read_text())
    assert next(j for j in again["jobs"] if j["id"] == data["jobs"][0]["id"])["outreach"]["draft_id"] == "r-9"
    assert "{company}" in again["outreach"]["linkedin_note"]
    assert again["outreach"]["profile"]["name"] == "Chakri"


def test_interactive_auth_loopback(monkeypatch):
    """Simulate the browser: Google redirects back to the local server with a code."""
    import urllib.parse
    import urllib.request

    def fake_open(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["access_type"] == ["offline"] and "gmail.compose" in q["scope"][0] and q["code_challenge_method"] == ["S256"]
        back = f"{q['redirect_uri'][0]}/?state={q['state'][0]}&code=abc123"
        urllib.request.urlopen(back, timeout=5).read()

    class TokenSession:
        def post(self, url, data=None, **kw):
            assert url == gmail.TOKEN_URL and data["code"] == "abc123" and data["code_verifier"]
            return Resp(200, {"refresh_token": "1//refresh", "access_token": "at"})

    monkeypatch.setattr(gmail.webbrowser, "open", fake_open)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    import builtins
    monkeypatch.setattr(builtins, "print", lambda *a, **k: None)
    # The real Timer fires the fake browser on another thread while the local server waits
    assert gmail.interactive_auth("cid", "sec", TokenSession()) == "1//refresh"
