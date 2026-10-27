"""Morning recruiter drafts: each opted-in user's Gmail gets drafts (never sent) for
recruiters who wrote an email address in a matching job posting.

Once a day (09:00 IST, GitHub Actions → `python -m jobradar.backend auto-drafts`), for
every user who turned on "Morning recruiter drafts":

1. Status of earlier drafts, read from that user's Gmail (labels and dates only):
   draft gone + a SENT message in its thread → "sent" (that date); draft gone and nothing
   sent → closed (you deleted it); someone else wrote in the thread after you → "replied".
2. Follow-ups: 5 working days after sending with no reply, a short follow-up draft is
   created as a reply in the same thread (once per job).
3. New drafts: the user's own matches (already filtered to their roles, cities and
   experience) whose posting text contains an email address. Skipped: addresses already
   contacted, excluded companies, no-reply style addresses, jobs older than 7 days, jobs
   you skipped or were rejected for. At most `auto_draft_limit` a day, best fit first.
   The text comes from the user's confirmed profile only; the resume they uploaded is attached.

Nothing is ever sent. Each user is isolated: a failure is recorded on that user only.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from .. import gmail, push, recruiter
from . import drafts, gmail_link, resume_intake
from .store import Conflict, Store

log = logging.getLogger("jobradar.autodraft")

FOLLOW_UP_DAYS = 5          # working days after sending
FRESH_DAYS = 7              # only postings found in the last week
TRACK_DAYS = 45             # stop checking a conversation after this long
GENERIC = re.compile(r"(^|[._-])(no-?reply|do-?not-?reply|noreply|mailer-daemon|notifications?|alerts?|support|privacy|abuse|"
                     r"webmaster|unsubscribe|feedback|newsletter|billing|legal|security)([._-]|@)", re.I)
SKIP_STATUS = {"skipped", "rejected", "offer"}


def working_days(since: datetime, now: datetime) -> int:
    """Mon–Fri days after `since` up to and including `now`'s date."""
    d, end, n = since.date(), now.date(), 0
    while d < end:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def _iso(ms: str | int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _parse(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _header(msg: dict, name: str) -> str:
    return next((h["value"] for h in (msg.get("payload") or {}).get("headers") or [] if h["name"].lower() == name.lower()), "")


def recipient_name(client: gmail.GmailClient, job: dict, email: str) -> tuple[str, str]:
    """(name, source) for the greeting: the post names them, else they've emailed you before, else the
    address is clearly a name. ("", "") when none of those: the draft then says "Hi,"."""
    named = next((p.get("name") for p in job.get("contact_people") or [] if p.get("email", "").lower() == email.lower()), "")
    if named:
        return named, "posting"
    try:
        for mid in client.list_message_ids(f"from:{email}", limit=1):
            name = recruiter.display_name(_header(client.message(mid), "From"), email)
            if name:
                return name, "gmail"
    except gmail.GmailApiError:
        pass  # a lookup that fails just means no name from Gmail
    name = recruiter.name_from_address(email)
    return (name, "address") if name else ("", "")


def usable(addr: str) -> bool:
    return drafts.valid_email(addr) and not GENERIC.search(addr.split("@")[0] + "@")


def candidates(jobs: list[dict], state: dict, profile: dict, now: datetime) -> list[tuple[dict, str]]:
    """(job, recruiter email) pairs to draft for, best fit first. Nothing here touches Gmail."""
    recs = (state.get("drafts") or {}).values()
    contacted = {str(d.get("to", "")).lower() for d in recs if d.get("to")}
    drafted = {d.get("job_id") for d in recs if d.get("type") in ("outreach", "application")}
    statuses = state.get("statuses") or {}
    excluded = [c.lower() for c in profile.get("excluded_companies") or []]
    out = []
    for job in sorted(jobs, key=lambda j: -(j.get("score") or 0)):
        if job.get("id") in drafted or (statuses.get(job.get("id")) or {}).get("status") in SKIP_STATUS:
            continue
        company = str(job.get("company") or "").lower()
        if any(x and (x in company or company in x) for x in excluded):
            continue
        found = _parse(job.get("first_seen")) or _parse(job.get("posted_date") and f"{job['posted_date'][:10]}T00:00:00+00:00")
        if not found or now - found > timedelta(days=FRESH_DAYS):
            continue
        email = next((e for e in job.get("contact_emails") or [] if usable(e) and e.lower() not in contacted), None)
        if email:
            contacted.add(email.lower())
            out.append((job, email))
    return out


def track(client: gmail.GmailClient, state: dict, now: datetime) -> tuple[dict, dict]:
    """Sent / replied / deleted for earlier outreach and follow-up drafts. Returns (patches by key, counts)."""
    patches, counts = {}, {"sent": 0, "replied": 0, "deleted": 0}
    recs = state.get("drafts") or {}
    for key, d in recs.items():
        if d.get("type") not in ("outreach", "application", "follow_up") or not d.get("id") or not d.get("thread_id"):
            continue
        if d.get("closed") or d.get("replied"):
            continue
        made = _parse(d.get("created") or d.get("at"))
        if made and now - made > timedelta(days=TRACK_DAYS):
            continue
        p = {}
        thread = None
        if not d.get("sent"):
            if client.draft_exists(d["id"]):
                continue
            thread = client.thread(d["thread_id"]) or {}
            mine = [m for m in thread.get("messages") or [] if "SENT" in (m.get("labelIds") or [])]
            if mine:
                p.update(sent=_iso(mine[-1].get("internalDate", 0)), sent_message_id=_header(mine[-1], "Message-ID"),
                         sent_subject=_header(mine[-1], "Subject") or d.get("subject", ""))
                counts["sent"] += 1
            else:
                p["closed"] = now.isoformat(timespec="seconds")
                p["note"] = d.get("note") or "Draft deleted in Gmail"
                counts["deleted"] += 1
                patches[key] = p
                continue
        sent_at = _parse(p.get("sent") or d.get("sent"))
        if sent_at and d.get("type") != "follow_up":
            thread = thread if thread is not None else (client.thread(d["thread_id"]) or {})
            theirs = [m for m in thread.get("messages") or [] if "SENT" not in (m.get("labelIds") or [])
                      and "DRAFT" not in (m.get("labelIds") or []) and _iso(m.get("internalDate", 0)) > sent_at.isoformat()]
            if theirs:
                p["replied"] = _iso(theirs[0].get("internalDate", 0))
                counts["replied"] += 1
        if p:
            patches[key] = p
    # A follow-up you sent marks the conversation "Follow-up sent"
    for key, p in list(patches.items()):
        d = recs[key]
        if d.get("type") == "follow_up" and p.get("sent"):
            main = next((k for k, x in recs.items() if x.get("job_id") == d.get("job_id") and x.get("type") in ("outreach", "application")), None)
            if main:
                patches.setdefault(main, {})["followup_sent"] = p["sent"]
    return patches, counts


def follow_ups_due(state: dict, patches: dict, now: datetime) -> list[tuple[str, dict]]:
    recs = state.get("drafts") or {}
    due = []
    for key, d in recs.items():
        d = {**d, **patches.get(key, {})}
        if d.get("type") not in ("outreach", "application") or not d.get("sent") or not d.get("to"):
            continue
        if d.get("replied") or d.get("closed") or d.get("followup_sent") or f"follow_up:{d.get('job_id')}" in recs:
            continue
        sent = _parse(d["sent"])
        if sent and working_days(sent, now) >= FOLLOW_UP_DAYS:
            due.append((key, d))
    return due


def run_user(store: Store, oauth: gmail_link.OAuthConfig, session, uid: str, now: datetime | None = None,
             vapid_key: str = "", vapid_subject: str = "") -> dict:
    """One user's morning drafts. Returns counts only."""
    from .tasks import read_state, write_state  # tasks imports this module's callers
    now = now or datetime.now(timezone.utc)
    profile = store.get("profiles", uid) or {}
    if not profile.get("auto_drafts") or profile.get("drafts_enabled") is False:
        return {"skipped": "off"}
    if not store.get("secrets", uid):
        return {"skipped": "gmail not connected"}
    report = {"created": 0, "follow_ups": 0, "sent": 0, "replied": 0, "deleted": 0, "candidates": 0}
    try:
        client = gmail_link.access_for(store, oauth, session, uid)
        state, _, _ = read_state(store, uid)
        patches, counts = track(client, state, now)
        report.update(counts)
        resume = resume_intake.load(store, uid)
        new: dict[str, dict] = {}
        scratch = {"drafts": dict(state.get("drafts") or {})}  # create() dedupes against this
        jobs = {j["id"]: j for j in (store.get("matches", uid) or {}).get("jobs") or []}
        for key, d in follow_ups_due(state, patches, now):
            job = jobs.get(d.get("job_id")) or (state.get("saved") or {}).get(d.get("job_id")) or {"id": d.get("job_id")}
            thread = (d["thread_id"], d.get("sent_message_id") or "", d.get("sent_subject") or d.get("subject") or "")
            rec = drafts.create(client, scratch, profile, "follow_up", job=job, to=d["to"], resume=resume, now=now,
                                thread=thread if thread[0] else None, auto=True,
                                to_name=d.get("to_name", ""), to_name_source=d.get("to_name_source", ""))
            if not rec.get("duplicate"):
                new[f"follow_up:{d.get('job_id')}"] = rec
                report["follow_ups"] += 1
        today = now.date().isoformat()
        made_today = sum(1 for d in scratch["drafts"].values() if d.get("auto") and d.get("type") == "outreach"
                         and str(d.get("created", "")).startswith(today))
        room = max(0, int(profile.get("auto_draft_limit") or 5) - made_today)
        todo = candidates(list(jobs.values()), state, profile, now)
        report["candidates"] = len(todo)
        for job, email in todo[:room]:
            name, source = recipient_name(client, job, email)
            rec = drafts.create(client, scratch, profile, "outreach", job=job, to=email, resume=resume, now=now, auto=True,
                                to_name=name, to_name_source=source)
            if not rec.get("duplicate"):
                new[f"outreach:{job['id']}"] = rec
                report["created"] += 1
        _save(store, uid, patches, new, now, read_state, write_state)
    except gmail.GmailAuthError as e:
        log.warning("auto drafts: Gmail sign-in failed for a user: %s", e)
        report["error"] = "auth"
    except (gmail.GmailApiError, drafts.DraftError) as e:
        log.warning("auto drafts failed for a user: %s", type(e).__name__)
        report["error"] = str(getattr(e, "user_message", "") or e)[:200]
    except Conflict:
        report["error"] = "the app kept saving at the same time"
    except Exception as e:  # noqa: BLE001 - one user's problem never stops the others
        log.exception("auto drafts failed for a user: %s", type(e).__name__)
        report["error"] = type(e).__name__
    store.update("gmail", uid, {"last_auto": {"at": now.isoformat(timespec="seconds"), **report}})
    if report.get("created") or report.get("follow_ups"):
        _notify(store, uid, report, vapid_key, vapid_subject, session)
    return report


def _save(store, uid, patches, new, now, read_state, write_state) -> None:
    """Merge the morning's changes into the user's state; redo the merge (not the Gmail work) on a clash."""
    if not patches and not new:
        return
    stamp = now.isoformat(timespec="seconds")
    for _ in range(4):
        state, version, doc = read_state(store, uid)
        recs = state.setdefault("drafts", {})
        for key, p in patches.items():
            if key in recs:
                recs[key] = {**recs[key], **p, "at": stamp}
        for key, rec in new.items():
            recs.setdefault(key, rec)
        try:
            write_state(store, uid, state, version, doc, now)
            return
        except Conflict:
            continue
    raise Conflict("state kept changing")


def _notify(store, uid, report, vapid_key, subject, session) -> None:
    subs = (store.get("push", uid) or {}).get("subscriptions") or []
    if not subs or not vapid_key or (store.get("profiles", uid) or {}).get("notify") is False:
        return
    n, f = report.get("created", 0), report.get("follow_ups", 0)
    body = ", ".join(x for x in (f"{n} recruiter draft{'s' if n != 1 else ''}" if n else "",
                                 f"{f} follow-up{'s' if f != 1 else ''}" if f else "") if x)
    payload = {"title": "Drafts ready in Gmail", "body": f"{body}. Review and send them yourself.", "url": "./#mail", "tag": "jobradar-drafts"}
    for sub in subs:
        try:
            push.send(sub, payload, vapid_key, subject, session)
        except Exception as e:  # noqa: BLE001 - a dead subscription is cleaned up by the alerts run
            log.warning("draft push failed: %s", type(e).__name__)


def run_all(store: Store, oauth: gmail_link.OAuthConfig, session, now: datetime | None = None,
            vapid_key: str = "", vapid_subject: str = "") -> dict:
    out = {"users": 0, "created": 0, "follow_ups": 0, "sent": 0, "replied": 0, "errors": 0}
    for uid in store.list_ids("secrets"):
        r = run_user(store, oauth, session, uid, now, vapid_key, vapid_subject)
        if r.get("skipped"):
            continue
        out["users"] += 1
        out["errors"] += "error" in r
        for k in ("created", "follow_ups", "sent", "replied"):
            out[k] += r.get(k, 0)
    return out
