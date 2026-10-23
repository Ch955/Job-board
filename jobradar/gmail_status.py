"""Read-only Gmail check: application confirmations, interview invites and
rejections move the matching job to Applied / Interview / Rejected.

Runs in GitHub Actions after each fetch. Reads only subject, sender, date and
Google's short snippet of recent job emails; never sends, labels or deletes.
Results go into the encrypted sync file, so they stay private.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

from . import gmail
from .models import norm_company

log = logging.getLogger("jobradar.gmail_status")

QUERY = ("newer_than:{days}d -category:promotions -from:jobalerts-noreply@linkedin.com "
         "(application OR applied OR applying OR interview OR assessment OR unfortunately OR regret OR shortlisted)")

# Job-alert digests and newsletters mention "apply" constantly; never treat them as your applications
DIGEST = re.compile(r"jobs? (you may|for you|matching|recommended|alert)|new jobs|job alert|recommended for you|"
                    r"apply now|are hiring|people also viewed|newsletter|webinar", re.I)
REJECTED = re.compile(r"unfortunately|regret to inform|not (be )?(moving|move) forward|decided to (move|proceed) "
                      r"(forward )?with other|not (been )?selected|position has been filled|other candidates", re.I)
INTERVIEW = re.compile(r"\binterview|schedule (a|your) (call|chat|conversation|discussion)|online (test|assessment)|"
                       r"\bassessment\b|coding (test|challenge)|hackerrank|codility|hirepro|mettl|shortlisted", re.I)
APPLIED = re.compile(r"application (was )?(sent|received|submitted|successful)|thank(s| you) for (applying|your "
                     r"(application|interest))|received your application|successfully applied|you('ve| have) applied|"
                     r"your application (to|for)", re.I)
# Where the email itself names the company (LinkedIn, most ATS confirmations)
COMPANY_IN_SUBJECT = [
    re.compile(r"your application was sent to (?P<company>.+?)\s*$", re.I),
    re.compile(r"thank(?:s| you) for applying (?:to|at) (?P<company>.+?)[!.]?\s*$", re.I),
    re.compile(r"your application (?:to|at|with) (?P<company>.+?)[!.]?\s*$", re.I),
    re.compile(r"application (?:for .+? )?(?:at|with) (?P<company>.+?)[!.]?\s*$", re.I),
]
TITLE_IN_SUBJECT = re.compile(r"(?:for|for the) (?:the )?(?P<title>.+?) (?:role|position|job|opening)?\s*(?:at|with|-|$)", re.I)
RANK = {"new": 0, "later": 0, "skipped": 0, "applied": 1, "interview": 2, "rejected": 3, "offer": 4}
STOP = {"data", "engineer", "senior", "the", "and", "for", "with", "india", "technologies", "solutions", "services",
        "limited", "private", "global", "group", "software", "consulting", "pvt", "ltd", "inc"}


def classify(subject: str, snippet: str, sender: str) -> str | None:
    text = f"{subject} {snippet}"
    if DIGEST.search(subject) or "jobalerts" in sender.lower():
        return None
    if REJECTED.search(text):
        return "rejected"
    if INTERVIEW.search(text):
        return "interview"
    if APPLIED.search(text):
        return "applied"
    return None


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def company_key(company: str) -> str:
    """Most distinctive word of a company name: 'Reveille Technologies,Inc' -> 'reveille'."""
    words = [w for w in norm_company(company).split() if w not in STOP]
    return max(words, key=len) if words else norm_company(company)


def match(jobs: list[dict], subject: str, snippet: str, sender: str) -> dict | None:
    """The job this email is about: company must appear, title words break ties."""
    hay = f"{subject} {snippet} {sender}".lower()
    hay_words = _words(hay)
    cands = []
    for j in jobs:
        key = company_key(j.get("company", ""))
        if len(key) >= 3 and re.search(rf"\b{re.escape(key)}", hay):
            cands.append((len(_words(j.get("title", "")) & hay_words), j))
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    if len(cands) > 1 and cands[0][0] == cands[1][0]:
        # Same company twice with no title clue: pick the one you already acted on, else give up
        acted = [c for c in cands if c[1].get("_status") not in (None, "new")]
        return acted[0][1] if len(acted) == 1 else None
    return cands[0][1]


def extract_company(subject: str) -> str | None:
    for rx in COMPANY_IN_SUBJECT:
        m = rx.search(subject.strip())
        if m:
            name = re.sub(r"\s*[|(\-–].*$", "", m.group("company")).strip(" .!")
            if 2 <= len(name) <= 60:
                return name
    return None


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers") or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def run(state: dict, jobs: list[dict], session, token: str, now: datetime | None = None, days: int = 3) -> dict:
    """Update `state` (sync format) in place from recent Gmail. Returns a report."""
    now = now or datetime.now(timezone.utc)
    g = state.setdefault("gmail", {})
    processed = list(g.get("processed") or [])
    seen = set(processed)
    events = list(g.get("events") or [])
    statuses = state.setdefault("statuses", {})
    saved = state.setdefault("saved", {})
    report = {"checked": 0, "updated": 0, "added": 0}

    # Jobs the app knows: the current list plus anything saved in sync
    known = {j["id"]: dict(j) for j in jobs}
    for jid, j in saved.items():
        known.setdefault(jid, dict(j))
    for jid, j in known.items():
        j["_status"] = (statuses.get(jid) or {}).get("status", "new")
    pool = list(known.values())

    auth = {"Authorization": f"Bearer {token}"}
    r = session.get(f"{gmail.API}/messages", headers=auth, params={"q": QUERY.format(days=days), "maxResults": 100})
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("messages", []) if m["id"] not in seen]
    for mid in reversed(ids):  # oldest first, so later emails win
        m = session.get(f"{gmail.API}/messages/{mid}", headers=auth,
                        params={"format": "metadata", "metadataHeaders": ["Subject", "From"]})
        m.raise_for_status()
        msg = m.json()
        report["checked"] += 1
        processed.append(mid)
        subject, sender, snippet = _header(msg, "Subject"), _header(msg, "From"), msg.get("snippet", "")
        status = classify(subject, snippet, sender)
        if not status:
            continue
        when = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
        job = match(pool, subject, snippet, sender)
        if job is None:
            company = extract_company(subject)
            if not company:
                continue
            jid = "mail-" + hashlib.sha1(f"{company.lower()}|{status}".encode()).hexdigest()[:10]
            title_m = TITLE_IN_SUBJECT.search(subject)
            job = {"id": jid, "title": title_m.group("title").strip() if title_m else "Application (from Gmail)",
                   "company": company, "location": "", "source": "email", "score": None, "matched_keywords": [],
                   "url": f"https://mail.google.com/mail/u/0/#all/{msg.get('threadId', mid)}", "_status": "new"}
            pool.append(job)
            report["added"] += 1
        cur = statuses.get(job["id"]) or {"status": job.get("_status", "new"), "at": ""}
        # Only move forward, and never over something you changed after this email arrived
        forward = RANK[status] > RANK.get(cur["status"], 0) and not (status == "rejected" and cur["status"] == "offer")
        if not forward or str(cur.get("at", "")) > when:
            continue
        rec = {"status": status, "at": when, "source": "gmail"}
        applied_at = cur.get("appliedAt") or when
        rec["appliedAt"] = applied_at
        statuses[job["id"]] = rec
        job["_status"] = status
        snap = {k: v for k, v in job.items() if not k.startswith("_")}
        saved[job["id"]] = {**saved.get(job["id"], {}), **snap}
        events.insert(0, {"at": when, "status": status, "job_id": job["id"], "company": job.get("company", ""),
                          "title": job.get("title", ""), "subject": subject[:140]})
        report["updated"] += 1

    g["processed"] = processed[-3000:]
    g["events"] = events[:60]
    g["last_check"] = now.isoformat(timespec="seconds")
    return report
