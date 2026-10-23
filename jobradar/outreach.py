"""Recruiter outreach: find contact emails in postings, write the email and the
LinkedIn note from templates in config.yaml. Plain string templates, no LLM.

Drafts are only ever *created* in your Gmail. Nothing is sent automatically.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from . import gmail, resume
from .config import env
from .models import Job

log = logging.getLogger("jobradar.outreach")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
# Addresses that never reach a person who can move your application
NOT_A_RECRUITER = re.compile(
    r"^(no-?reply|do-?not-?reply|privacy|support|help|info|feedback|abuse|security|legal|"
    r"compliance|accommodations?|accessibility|grievance|unsubscribe|webmaster|admin|press|media|"
    r"sales|billing|accounts?)@|@(example\.|sentry\.|.*\.png$|.*\.jpg$)",
    re.I,
)
# Job boards' own addresses, not the hiring company's
BOARD_DOMAINS = ("naukri.com", "linkedin.com", "indeed.com", "glassdoor.", "foundit.", "monster.", "shine.com")


def extract_emails(text: str) -> list[str]:
    seen: list[str] = []
    for m in EMAIL_RE.finditer(text or ""):
        email = m.group(0).strip(".").lower()
        if NOT_A_RECRUITER.search(email) or any(d in email.split("@")[1] for d in BOARD_DOMAINS):
            continue
        if email not in seen:
            seen.append(email)
    return seen[:3]


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def fields(job: Job, config: dict) -> dict:
    p = config.get("profile", {})
    skills = [k for k in job.matched_keywords if k not in ("Azure", "SQL", "Python")][:3] or ["ADF", "Databricks", "PySpark"]
    return _SafeDict(
        name=p.get("name", ""),
        full_name=p.get("full_name") or p.get("name", ""),
        role=p.get("role", "Data Engineer"),
        years=p.get("years_experience", ""),
        notice=p.get("notice", ""),
        notice_lower=(p.get("notice", "") or "")[:1].lower() + (p.get("notice", "") or "")[1:],
        email=p.get("apply_email", ""),
        linkedin=p.get("linkedin", ""),
        title=job.title,
        company=job.company,
        location=job.score_breakdown.get("location_label") or job.location,
        skills=", ".join(skills),
        url=job.url,
    )


def render(template: str, job: Job, config: dict) -> str:
    return template.format_map(fields(job, config)).strip()


def email_for(job: Job, config: dict) -> tuple[str, str]:
    t = config.get("outreach", {}).get("email", {})
    signature = env("EMAIL_SIGNATURE")  # optional secret, e.g. with your phone number
    body = render(t.get("body", ""), job, config)
    if signature:
        body += "\n" + signature.replace("\\n", "\n")
    return render(t.get("subject", "{title}"), job, config), body


def linkedin_note(job: Job, config: dict, limit: int = 200) -> str:
    """Fit LinkedIn's limit by shortening the job title, so the sign-off survives."""
    template = config.get("outreach", {}).get("linkedin_note", "")
    note = render(template, job, config)
    if len(note) > limit and "{title}" in template:
        keep = max(10, len(job.title) - (len(note) - limit) - 1)
        short = Job(**{**job.__dict__, "title": job.title[:keep].rstrip(" -,|/") + "…"})
        note = render(template, short, config)
    return note if len(note) <= limit else note[: limit - 1].rstrip() + "…"


def publish_templates(config: dict) -> dict:
    """What the app needs to build LinkedIn notes on the phone (no secrets in here)."""
    p = config.get("profile", {})
    o = config.get("outreach", {})
    return {
        "linkedin_note": o.get("linkedin_note", ""),
        "email_subject": (o.get("email") or {}).get("subject", ""),
        "email_body": (o.get("email") or {}).get("body", ""),
        "profile": {k: p.get(k, "") for k in ("name", "full_name", "role", "years_experience", "notice", "linkedin")},
    }


# --------------------------------------------------------------- drafting
def gmail_configured() -> str | None:
    missing = [k for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN") if not env(k)]
    return f"{', '.join(missing)} not set" if missing else None


def create_drafts(jobs: list[Job], state: dict, config: dict, session, now: datetime | None = None) -> dict:
    """Draft one email per strong new job that lists a recruiter email.

    Never the same job twice, never the same address twice within `cooldown_days`.
    Returns a report; never raises (a Gmail problem must not lose the run's jobs).
    """
    now = now or datetime.now(timezone.utc)
    o = config.get("outreach", {})
    report = {"created": 0, "skipped": None, "errors": 0, "candidates": 0}
    if not o.get("drafts_enabled", True):
        report["skipped"] = "disabled in config"
        return report
    reason = gmail_configured()
    if reason:
        report["skipped"] = reason
        return report

    min_score = int(o.get("min_score", config.get("pipeline", {}).get("alert_score", 70)))
    max_per_run = int(o.get("max_drafts_per_run", 10))
    cooldown = timedelta(days=int(o.get("cooldown_days", 30)))
    emailed: dict = state.setdefault("emailed", {})
    # Forget old cooldowns so state doesn't grow forever
    for addr, when in list(emailed.items()):
        if now - datetime.fromisoformat(when) > cooldown:
            del emailed[addr]

    todo, claimed = [], set(emailed)
    # Best jobs first, so the per-run cap and the one-draft-per-address rule favour them
    for job in sorted(jobs, key=lambda j: -j.score):
        if job.outreach.get("draft_id") or job.status not in ("new", "later") or job.score < min_score:
            continue
        to = next((e for e in job.contact_emails if e not in claimed), None)
        if to:
            claimed.add(to)
            todo.append((job, to))
    report["candidates"] = len(todo)
    if not todo:
        return report

    try:
        token = gmail.access_token(session, env("GMAIL_CLIENT_ID"), env("GMAIL_CLIENT_SECRET"), env("GMAIL_REFRESH_TOKEN"))
    except Exception as e:  # noqa: BLE001
        report["skipped"] = f"Gmail sign-in failed: {e}"[:200]
        return report

    attachment = None
    try:
        attachment = resume.load()
    except Exception as e:  # noqa: BLE001
        log.warning("resume not attached: %s", e)
    if attachment is None:
        report["no_resume"] = True

    for job, to in todo[:max_per_run]:
        subject, body = email_for(job, config)
        try:
            draft = gmail.create_draft(session, token, to=to, subject=subject, body=body, attachment=attachment,
                                       sender=config.get("profile", {}).get("apply_email"))
        except Exception as e:  # noqa: BLE001
            report["errors"] += 1
            log.warning("draft for %s failed: %s", job.company, type(e).__name__)
            continue
        job.outreach = {"email": to, "draft_id": draft["id"], "message_id": draft.get("message_id", ""),
                        "drafted_at": now.isoformat(timespec="seconds"), "subject": subject}
        emailed[to] = now.isoformat(timespec="seconds")
        report["created"] += 1
    return report
