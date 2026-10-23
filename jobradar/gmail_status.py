"""Read-only Gmail check: job emails become application-status updates.

Each run reads only recent job-related mail (incremental, paginated), classifies
each message (confirmation, interview, assessment, rejection, offer, recruiter
reply...) with a confidence, and matches it to one of the user's jobs. A status is
changed only when both the classification and the job match are confident; anything
uncertain goes to a review list the user resolves in the app. Message bodies are
read only when the subject isn't clear, and never stored: an event keeps the type,
time, sender name, a short subject and ids.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from . import gmail
from .models import norm_company

log = logging.getLogger("jobradar.gmail_status")

BASE_QUERY = ("-category:promotions -category:social -from:jobalerts-noreply@linkedin.com "
              "(application OR applied OR applying OR interview OR assessment OR test OR unfortunately OR regret "
              "OR shortlisted OR offer OR candidature OR \"next steps\" OR recruiter OR position OR role)")

DIGEST = re.compile(r"jobs? (you may|for you|matching|recommended|alert)|new jobs|job alert|recommended for you|"
                    r"apply now|are hiring|people also viewed|newsletter|webinar|top job picks|jobs near you|"
                    r"salary insights|profile views|who viewed", re.I)
NOREPLY = re.compile(r"no-?reply|do-?not-?reply|notifications?@|mailer", re.I)
ATS_SENDERS = re.compile(r"greenhouse|lever\.co|ashbyhq|smartrecruiters|workday|myworkday|icims|taleo|successfactors|"
                         r"jobvite|naukri|linkedin|indeed|hackerrank|codility|hirepro|mettl|talent500|instahyre|hirist", re.I)

# (event type, pattern, strong) - strong patterns are decisive when they appear in the subject
PATTERNS = [
    ("offer", re.compile(r"\boffer letter\b|pleased to (offer|extend)|job offer|offer of employment|congratulations.{0,40}offer", re.I), True),
    ("rejected", re.compile(r"unfortunately|regret to inform|not (be )?(moving|move) forward|decided to (move|proceed) "
                            r"(forward )?with other|not (been )?selected|position has been filled|other candidates|"
                            r"will not be progressing|not progressing", re.I), True),
    ("assessment", re.compile(r"online (test|assessment)|coding (test|challenge|assessment)|technical assessment|"
                              r"hackerrank|codility|hirepro|mettl|take[- ]home|complete the (test|assessment)", re.I), True),
    ("scheduling", re.compile(r"schedule (a|your) (call|chat|conversation|discussion|interview)|share your availability|"
                              r"(pick|choose|book) a (slot|time)|calendly|interview (slot|availability)", re.I), True),
    ("interview", re.compile(r"\binterview (invitation|invite|scheduled|confirmation|round)|invit(ed|ation) (you )?to "
                             r"(an |a )?interview|\binterview\b", re.I), False),
    ("shortlisted", re.compile(r"shortlisted|moved to the next (round|stage)|next steps in (the|your) (process|application)", re.I), True),
    ("applied", re.compile(r"application (was )?(sent|received|submitted|successful)|your application (to|for|was)|"
                           r"successfully applied|you('ve| have) applied", re.I), True),
    ("acknowledged", re.compile(r"thank(s| you) for (applying|your (application|interest))|received your (application|resume|cv)|"
                                r"we('ve| have) received", re.I), True),
]
STATUS_FOR = {"applied": "applied", "acknowledged": "applied", "shortlisted": "interview", "assessment": "interview",
              "scheduling": "interview", "interview": "interview", "rejected": "rejected", "offer": "offer"}
REPLY_HINT = re.compile(r"please (share|send|confirm|let me know)|your (availability|notice period|current ctc|expected ctc)|"
                        r"can you|could you|are you (available|interested)|kindly (share|send)", re.I)
COMPANY_IN_SUBJECT = [
    re.compile(r"your application was sent to (?P<company>.+?)\s*$", re.I),
    re.compile(r"thank(?:s| you) for applying (?:to|at) (?P<company>.+?)[!.]?\s*$", re.I),
    re.compile(r"your application (?:to|at|with) (?P<company>.+?)[!.]?\s*$", re.I),
    re.compile(r"application (?:for .+? )?(?:at|with) (?P<company>.+?)[!.]?\s*$", re.I),
]
TITLE_IN_SUBJECT = re.compile(r"(?:for|for the) (?:the )?(?P<title>.+?) (?:role|position|job|opening)?\s*(?:at|with|-|$)", re.I)
RANK = {"new": 0, "later": 0, "skipped": 0, "applied": 1, "interview": 2, "rejected": 3, "offer": 4}
STOP = {"data", "engineer", "senior", "the", "and", "for", "with", "india", "technologies", "solutions", "services",
        "limited", "private", "global", "group", "software", "consulting", "pvt", "ltd", "inc", "role", "job",
        "position", "your", "application", "team"}


# ------------------------------------------------------------------ classify
def classify(subject: str, snippet: str, sender: str, body: str = "") -> str | None:
    """Back-compat helper: the event type only."""
    return classify_ex(subject, snippet, sender, body)[0]


def classify_ex(subject: str, snippet: str, sender: str, body: str = "") -> tuple[str | None, str]:
    """(event type or None, confidence 'high' | 'medium' | 'low')."""
    if DIGEST.search(subject) or "jobalerts" in sender.lower():
        return None, "low"
    for where, text in (("subject", subject), ("snippet", snippet), ("body", body)):
        if not text:
            continue
        for kind, rx, strong in PATTERNS:
            if rx.search(text):
                if where == "subject":
                    conf = "high" if strong or ATS_SENDERS.search(sender) else "medium"
                else:
                    conf = "high" if strong and ATS_SENDERS.search(sender) else "medium"
                return kind, conf
    if subject.lower().startswith("re:") and not NOREPLY.search(sender) and REPLY_HINT.search(f"{snippet} {body}"):
        return "recruiter_reply", "medium"
    return None, "low"


# --------------------------------------------------------------------- match
def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in STOP}


def company_key(company: str) -> str:
    """The distinctive part of a company name as a phrase ("Tiger Analytics" stays whole, so a generic
    word like "analytics" never matches on its own; "Celebal Technologies" -> "celebal")."""
    words = [w for w in norm_company(company).split() if w not in STOP]
    return " ".join(words) if words else norm_company(company)


def match_ex(jobs: list[dict], text: str, sender: str, thread_id: str = "", threads: dict | None = None
             ) -> tuple[dict | None, str, list[dict]]:
    """(job, confidence, candidates). Confidence 'high' only with evidence beyond the company name:
    the email's thread is one we drafted in, the job title shows up, or it's the only job at that
    company and you'd already applied to or opened it."""
    if thread_id and threads and threads.get(thread_id):
        jid = threads[thread_id]
        job = next((j for j in jobs if j["id"] == jid), None)
        if job:
            return job, "high", [job]
    hay = f"{text} {sender}".lower()
    hay_words = _words(hay)
    cands = []
    for j in jobs:
        key = company_key(j.get("company", ""))
        if len(key) >= 3 and re.search(rf"\b{re.escape(key)}\b", hay):
            tw = _words(j.get("title", ""))
            overlap = len(tw & hay_words) / max(1, len(tw)) if tw else 0
            cands.append((overlap, j))
    if not cands:
        return None, "none", []
    cands.sort(key=lambda c: -c[0])
    best_overlap, best = cands[0]
    tie = len(cands) > 1 and cands[1][0] == best_overlap
    if best_overlap >= 0.6 and not tie:
        return best, "high", [c[1] for c in cands]
    if len(cands) == 1 and (best.get("_status") not in (None, "new", "skipped") or best.get("_pending")):
        return best, "high", [best]
    if len(cands) == 1:
        return best, "medium", [best]
    return None, "ambiguous", [c[1] for c in cands]


def match(jobs: list[dict], subject: str, snippet: str, sender: str) -> dict | None:
    """Back-compat: the job only when the match is confident."""
    job, conf, _ = match_ex(jobs, f"{subject} {snippet}", sender)
    return job if conf == "high" else None


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


def _sender_name(sender: str) -> str:
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', sender)
    return (m.group(1) if m else sender.split("@")[0]).strip()[:60]


# ----------------------------------------------------------------------- run
def run(state: dict, jobs: list[dict], client, token: str | None = None, now: datetime | None = None, days: int = 3,
        limit: int = 300, read_bodies: bool = True) -> dict:
    """Update `state` (sync format) in place from this user's recent Gmail. Returns a report.

    `client` is a gmail.GmailClient (or, for the old single-user CLI, a requests session + `token`).
    `last_check` is written only after every message was handled, so a failed run never looks successful.
    """
    if not isinstance(client, gmail.GmailClient):
        client = gmail.GmailClient(client, token)
    now = now or datetime.now(timezone.utc)
    g = state.setdefault("gmail", {})
    processed = list(g.get("processed") or [])
    seen = set(processed)
    events = list(g.get("events") or [])
    review = list(g.get("review") or [])
    resolved = state.get("reviews") or {}
    statuses = state.setdefault("statuses", {})
    saved = state.setdefault("saved", {})
    threads = {d.get("thread_id"): d.get("job_id") for d in (state.get("drafts") or {}).values() if d.get("thread_id")}
    report = {"checked": 0, "updated": 0, "added": 0, "review": 0, "replies": 0}

    known = {j["id"]: dict(j) for j in jobs}
    for jid, j in saved.items():
        known.setdefault(jid, dict(j))
    for jid, j in known.items():
        j["_status"] = (statuses.get(jid) or {}).get("status", "new")
        j["_pending"] = bool((state.get("pending") or {}).get(jid, {}).get("open"))
    pool = list(known.values())

    if g.get("last_check"):
        since = datetime.fromisoformat(g["last_check"]) - timedelta(days=1)  # overlap; processed ids dedupe
        query = f"after:{int(since.timestamp())} {BASE_QUERY}"
    else:
        query = f"newer_than:{max(days, 14)}d {BASE_QUERY}"
    ids = [m for m in client.list_message_ids(query, limit=limit) if m not in seen]

    for mid in reversed(ids):  # oldest first, so later emails win
        msg = client.message(mid)
        report["checked"] += 1
        processed.append(mid)
        subject, sender, snippet = _header(msg, "Subject"), _header(msg, "From"), msg.get("snippet", "")
        kind, conf = classify_ex(subject, snippet, sender)
        body = ""
        job, mconf, cands = match_ex(pool, f"{subject} {snippet}", sender, msg.get("threadId", ""), threads)
        # Not sure yet: read the body once (not stored) for a clearer signal or the job title
        unclear = kind is None and ATS_SENDERS.search(sender) or kind and (conf != "high" or mconf in ("medium", "ambiguous"))
        if read_bodies and unclear:
            body = gmail.message_text(client.message(mid, full=True))
            kind, conf = classify_ex(subject, snippet, sender, body)
            job, mconf, cands = match_ex(pool, f"{subject} {snippet} {body}", sender, msg.get("threadId", ""), threads)
        if not kind:
            continue
        when = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
        event = {"id": mid, "at": when, "type": kind, "confidence": conf, "thread_id": msg.get("threadId", ""),
                 "from": _sender_name(sender), "subject": subject[:140], "job_id": job["id"] if job else None,
                 "company": (job or {}).get("company") or extract_company(subject) or _sender_name(sender), "title": (job or {}).get("title", "")}

        if kind == "recruiter_reply":
            event["needs_reply"] = True
            events.insert(0, event)
            report["replies"] += 1
            continue

        status = STATUS_FOR[kind]
        if job is None and mconf == "none":
            company = extract_company(subject)
            if company and conf == "high" and kind in ("applied", "acknowledged"):
                # An application made outside the app (e.g. LinkedIn Easy Apply): add it rather than guess
                jid = "mail-" + hashlib.sha1(company.lower().encode()).hexdigest()[:10]
                title_m = TITLE_IN_SUBJECT.search(subject)
                job = {"id": jid, "title": title_m.group("title").strip() if title_m else "Application (from Gmail)",
                       "company": company, "location": "", "source": "email", "score": None, "matched_keywords": [],
                       "url": f"https://mail.google.com/mail/u/0/#all/{msg.get('threadId', mid)}", "_status": "new"}
                pool.append(job)
                mconf = "high"
                event.update(job_id=jid, company=company, title=job["title"])
                report["added"] += 1
            else:
                events.insert(0, {**event, "applied": False})
                continue

        if mconf != "high" or conf == "low":
            rid = f"r-{mid}"
            if rid not in resolved and not any(r["id"] == rid for r in review):
                review.insert(0, {**event, "id": rid, "message_id": mid, "status": status,
                                  "candidates": [{"id": c["id"], "title": c.get("title", ""), "company": c.get("company", "")} for c in cands[:5]],
                                  "reason": "Several jobs at this company" if mconf == "ambiguous" else "Only the company name matched"})
                report["review"] += 1
            continue

        cur = statuses.get(job["id"]) or {"status": job.get("_status", "new"), "at": ""}
        forward = RANK[status] > RANK.get(cur["status"], 0) and not (status == "rejected" and cur["status"] == "offer")
        if not forward or str(cur.get("at", "")) > when:  # never over something you changed later
            events.insert(0, {**event, "applied": False})
            continue
        statuses[job["id"]] = {"status": status, "at": when, "source": "gmail", "appliedAt": cur.get("appliedAt") or when,
                               "message_id": mid}
        job["_status"] = status
        saved[job["id"]] = {**saved.get(job["id"], {}), **{k: v for k, v in job.items() if not k.startswith("_")}}
        events.insert(0, {**event, "applied": True, "status": status})
        report["updated"] += 1

    g["processed"] = processed[-3000:]
    g["events"] = events[:100]
    g["review"] = [r for r in review if r["id"] not in resolved][:50]
    g["last_check"] = now.isoformat(timespec="seconds")
    return report
