"""Scheduled work for every user: refresh the shared pool, score it per user,
sync each user's own Gmail, and alert each user about their own new matches.

Each user is handled in isolation: one user's failure (revoked Gmail, bad data)
is recorded on their status and never stops or leaks into another user's run.
"""

from __future__ import annotations

import copy
from collections import Counter
import json
import re
import logging
from datetime import datetime, timezone

from .. import apply_type, gmail, gmail_status, outreach, push, skills_vocab
from ..models import Job
from ..pipeline import collapse, dedupe, fetch_all, permitted
from ..scoring import Scorer, age_days
from . import gmail_link, walkins
from .matching import UserMatcher, _role_patterns
from .profile import is_complete
from .store import Conflict, Store

log = logging.getLogger("jobradar.tasks")
POOL_CHUNK = 120          # jobs per pool document (Firestore docs are capped at 1 MiB)
DESC_CHARS = 2500         # enough of the description for skill matching
WALKIN_DESC_CHARS = 6000
MAX_MATCHES = 150
MAX_QUERIES = 6


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def profiles(store: Store) -> dict[str, dict]:
    out = {}
    for uid in store.list_ids("profiles"):
        p = store.get("profiles", uid)
        if p and not is_complete(p):
            out[uid] = p
    return out


# ------------------------------------------------------------------- pool
def roles_of(p: dict) -> list[str]:
    return [r for r in (p.get("target_roles") or [p.get("current_role", "")]) if r]


def all_roles(users: dict[str, dict], first: str | None = None) -> list[str]:
    """Every user's target roles, deduplicated; `first` user's roles go first (searched first)."""
    roles: list[str] = []
    order = ([users[first]] if first in users else []) + [p for u, p in users.items() if u != first]
    for p in order:
        for r in roles_of(p):
            if r.lower() not in (x.lower() for x in roles):
                roles.append(r)
    return roles


def pool_status(store: Store, profile: dict | None = None, now: datetime | None = None,
                max_age_hours: float = 1.0) -> str | None:
    """Why the shared pool should be refreshed now (None = it's fine)."""
    meta = store.get("pool", "meta") or {}
    if not meta.get("count"):
        return "empty"
    try:
        age = (_now(now) - datetime.fromisoformat(meta["updated"])).total_seconds() / 3600
    except (KeyError, ValueError):
        return "empty"
    covered = {r.lower() for r in meta.get("roles", [])}
    if profile and any(r.lower() not in covered for r in roles_of(profile)):
        return "new roles"
    return "stale" if age >= max_age_hours else None


def pool_config(base: dict, users: dict[str, dict], first: str | None = None) -> dict:
    """Search for every user's target roles; keep any posting at least one user could want."""
    roles = all_roles(users, first)
    cfg = copy.deepcopy(base)
    if roles:
        queries = roles[:MAX_QUERIES]
        src = cfg.setdefault("sources", {})
        for name in ("jobspy", "adzuna", "jooble"):
            if name in src:
                src[name]["queries"] = queries
        if "smartrecruiters" in src:
            src["smartrecruiters"]["query"] = ""
        cfg["title"] = {"rules": [{"pattern": p.pattern, "points": pts} for p, pts, _ in _role_patterns(roles) if pts >= 18],
                        "exclude": [], "penalties": []}
    return cfg


def refresh_pool(store: Store, base: dict, session, now: datetime | None = None, fetcher=fetch_all,
                 first: str | None = None) -> dict:
    now = _now(now)
    users = profiles(store)
    cfg = pool_config(base, users, first)
    meta = store.get("pool", "meta") or {}
    fetch_state = meta.get("fetch_state") or {}
    jobs, runs = fetcher(cfg, session, fetch_state, now=now)
    scorer = Scorer(cfg)
    kept = []
    for job in dedupe(jobs):
        job.first_seen = job.first_seen or _iso(now)
        if not scorer.is_relevant(job) or (age_days(job, now) or 0) > 30 or not scorer.location_points(job)[2]:
            continue
        apply_type.annotate(job)
        job.contact_emails = outreach.extract_emails(job.description) or job.contact_emails  # "send your resume to …"
        d = job.to_dict()
        # Walk-in details (date, venue, documents) are often further down: keep more of those
        d["description"] = (job.description or "")[:WALKIN_DESC_CHARS if walkins.is_walkin(job, cfg) else DESC_CHARS]
        kept.append(d)
    # Keep last run's jobs that are still recent, so a source outage doesn't empty everyone's list
    have = {d["id"] for d in kept}
    for old in load_pool(store):
        if old.id not in have and (age_days(old, now) or 0) <= 30 and permitted(cfg, old.source):
            if not old.apply_type:
                apply_type.annotate(old)
            d = old.to_dict()
            d["description"] = old.description[:DESC_CHARS]
            kept.append(d)
    kept = collapse(kept)  # the same posting from LinkedIn, Naukri and the careers site is one job
    chunks = [kept[i:i + POOL_CHUNK] for i in range(0, len(kept), POOL_CHUNK)] or [[]]
    for i, c in enumerate(chunks):
        store.set("pool", f"c{i}", {"jobs": c})
    for i in range(len(chunks), int(meta.get("chunks", 0))):
        store.delete("pool", f"c{i}")
    store.set("pool", "meta", {"chunks": len(chunks), "count": len(kept), "updated": _iso(now),
                               "fetch_state": fetch_state, "sources": runs,
                               # every role wanted at this point counts as covered, so a role beyond the
                               # query cap doesn't trigger a new search on every refresh
                               "roles": all_roles(users, first),
                               "by_site": dict(Counter(d.get("source", "?") for d in kept).most_common())})
    return {"jobs": len(kept), "sources": {k: v.get("status") for k, v in runs.items()}}


def load_pool(store: Store) -> list[Job]:
    meta = store.get("pool", "meta") or {}
    out = []
    for i in range(int(meta.get("chunks", 0))):
        for d in (store.get("pool", f"c{i}") or {}).get("jobs", []):
            j = Job.from_dict(d)
            j.description = d.get("description", "")
            out.append(j)
    return out


# ---------------------------------------------------------------- matches
def match_user(store: Store, base: dict, uid: str, pool: list[Job] | None = None, now: datetime | None = None,
               profile: dict | None = None) -> dict:
    now = _now(now)
    p = profile or store.get("profiles", uid)
    if not p or is_complete(p):
        return {"skipped": "profile incomplete"}
    pool = load_pool(store) if pool is None else pool
    matcher = UserMatcher(base, p)
    pool = [j for j in pool if permitted(base, j.source)]  # switched-off portals never reach anyone's list
    jobs = matcher.match(pool, now)[:MAX_MATCHES]
    old = store.get("matches", uid) or {}
    seen = set(old.get("seen") or [])
    new = [j for j in jobs if j["id"] not in seen]
    for j in jobs:
        j.pop("description", None)
    store.set("matches", uid, {"jobs": jobs, "threshold": p.get("threshold", 70), "updated": _iso(now),
                               "count": len(jobs), "seen": sorted(seen | {j["id"] for j in jobs})[-3000:],
                               "skills_scan": skill_scan(matcher, pool, p, now)})
    return {"count": len(jobs), "new": [j["id"] for j in new]}


SCAN_DAYS = 30
NOT_FAILED = ("ok", "disabled", "not permitted", "throttled", "skipped")
# Known blocks, in plain words (the raw error is kept after a dash for the curious)
KNOWN_BLOCKS = [
    (re.compile(r"naukri.*406|406.*naukri|request token", re.I),
     "Naukri is refusing automated searches (it changed its request check). Nothing to fix on your side; "
     "Naukri jobs return when the JobSpy library is updated"),
    (re.compile(r"google returned no job data", re.I), "Google Jobs isn't returning results to automated searches"),
    (re.compile(r"glassdoor.*403|403.*glassdoor", re.I), "Glassdoor blocks searches from the server (403)"),
]


def _plain(source: str, reason: str) -> str:
    for rx, text in KNOWN_BLOCKS:
        if rx.search(f"{source} {reason}"):
            return text
    return reason


def failed_sources(runs: dict) -> list[dict]:
    """Sources that broke in the last pool run: a whole source erroring, or JobSpy sites whose searches failed.

    One company's careers board missing (a 404 on Greenhouse etc.) isn't a source failure, so it isn't listed.
    """
    out = []
    for name, r in runs.items():
        if r.get("status") not in NOT_FAILED:
            out.append({"source": name, "reason": _plain(name, str(r.get("error") or r.get("status")))[:200]})
        elif name == "jobspy":
            sites: dict = {}
            for w in r.get("warnings") or []:
                site = w.split(" ", 1)[0]
                sites.setdefault(site, w.split(": ", 1)[-1][:160])
            out += [{"source": site, "reason": _plain(site, why)[:200]} for site, why in sites.items()]
    return out


def skill_scan(matcher: UserMatcher, pool: list[Job], p: dict, now: datetime) -> dict:
    """Which skills recent postings for your roles and experience ask for, and how many of those mentions you cover.

    Counted over real pool postings only: role related to yours, experience range overlapping yours, last 30 days.
    Each posting counts a skill once.
    """
    s = matcher.scorer
    counts: Counter = Counter()
    scanned = 0
    for job in pool:
        if (age_days(job, now) or 0) > SCAN_DAYS or s.title_points(job.title) < 18:
            continue
        text = job.description or job.description_snippet
        if not s.experience_points(text)[3]:
            continue
        scanned += 1
        counts.update(set(skills_vocab.find_skills(f"{job.title} {text}")))
    mine = skills_vocab.covered(p.get("skills") or [])
    total = sum(counts.values())
    covered = sum(n for k, n in counts.items() if k.lower() in mine)
    lo, hi = matcher.cfg["experience"]["min_years"], matcher.cfg["experience"]["max_years"]
    return {"scanned": scanned, "days": SCAN_DAYS, "band": [lo, hi], "updated": _iso(now),
            "coverage": round(100 * covered / total) if total else None, "mentions": total,
            # objects, not [name, count] pairs: Firestore can't store a list inside a list
            "skills": [{"skill": k, "count": n} for k, n in counts.most_common(80)],
            "implied": sorted(k for k in counts if k.lower() in mine and k.lower() not in {x.lower() for x in p.get("skills") or []})}


def notify_user(store: Store, uid: str, new_jobs: list[dict], vapid_key: str, subject: str, session) -> int:
    p = store.get("profiles", uid) or {}
    subs = (store.get("push", uid) or {}).get("subscriptions") or []
    if not new_jobs or not subs or not vapid_key or p.get("notify") is False:
        return 0
    top = new_jobs[0]
    more = f" (+{len(new_jobs) - 1} more)" if len(new_jobs) > 1 else ""
    payload = {"title": f"{top['score']} · {top['title']}"[:80], "body": f"{top['company']}{more}"[:120],
               "url": "./#matches", "tag": "jobradar-new"}
    alive, sent = [], 0
    for sub in subs:
        try:
            push.send(sub, payload, vapid_key, subject, session)
            sent += 1
            alive.append(sub)
        except push.PushGone:
            continue
        except Exception as e:  # noqa: BLE001 - try again next run
            log.warning("push failed for one subscription: %s", type(e).__name__)
            alive.append(sub)
    if len(alive) != len(subs):
        store.set("push", uid, {"subscriptions": alive})
    return sent


# ------------------------------------------------------------------ gmail
def read_state(store: Store, uid: str) -> tuple[dict, str | None, dict | None]:
    doc, version = store.get_versioned("users", uid)
    state = json.loads(doc["state"]) if doc and doc.get("state") else {}
    return state, version, doc


def write_state(store: Store, uid: str, state: dict, version: str | None, doc: dict | None, now: datetime) -> None:
    new = {**(doc or {}), "state": json.dumps(state, separators=(",", ":")), "updated": _iso(now)}
    if version:
        store.set("users", uid, new, if_version=version)
    else:
        store.set("users", uid, new, if_missing=True)


def _tag(uid: str) -> str:
    import hashlib
    return "user-" + hashlib.sha256(uid.encode()).hexdigest()[:10]


def gmail_sync_user(store: Store, cfg: gmail_link.OAuthConfig, session, uid: str, now: datetime | None = None,
                    days: int = 14) -> dict:
    """Sync one user's Gmail into that user's own state. Status records the outcome either way."""
    now = _now(now)
    status = {"last_attempt": _iso(now)}
    try:
        client = gmail_link.access_for(store, cfg, session, uid)
        matches = (store.get("matches", uid) or {}).get("jobs") or []
        for _ in range(3):
            state, version, doc = read_state(store, uid)
            state = copy.deepcopy(state)
            report = gmail_status.run(state, matches, client, now=now, days=days)
            try:
                write_state(store, uid, state, version, doc, now)
                break
            except Conflict:
                continue  # the app saved at the same moment: read again and redo
        else:
            raise RuntimeError("the app kept saving at the same time; will retry next run")
        status.update(last_success=_iso(now), last_error=None, last_report=report,
                      note=(f"{report['remaining']} more emails to read: they're picked up on the next sync "
                            "(Gmail allows only so many reads a minute)." if report.get("partial") else None))
        store.update("gmail", uid, status)
        return report
    except gmail.GmailAuthError as e:
        # Logged: our own message (no token or email data). Stored: what the user can do about it.
        log.warning("gmail sync failed for %s: GmailAuthError: %s", _tag(uid), e)
        if (store.get("gmail", uid) or {}).get("connected") is not False:  # else access_for already disconnected + explained
            status["last_error"] = ("Gmail didn't allow reading your mail (permission missing). Disconnect Gmail in Settings, "
                                    "then connect again and tick both boxes." if e.revoked else "Gmail sign-in failed; will retry.")
            store.update("gmail", uid, status)
        return {"error": "auth"}
    except gmail.GmailApiError as e:
        log.warning("gmail sync failed for %s: %s", _tag(uid), e)   # Google's status, reason and message only
        status["last_error"] = e.user_message
        store.update("gmail", uid, status)
        return {"error": f"gmail {e.status}"}
    except Exception as e:  # noqa: BLE001 - isolate users from each other
        log.exception("gmail sync failed for %s: %s", _tag(uid), type(e).__name__)  # traceback: code lines, no values
        status["last_error"] = f"Gmail sync failed ({type(e).__name__}); will retry."
        store.update("gmail", uid, status)
        return {"error": type(e).__name__}


def run_all(store: Store, base: dict, session, *, oauth: gmail_link.OAuthConfig | None = None, vapid_key: str = "",
            vapid_subject: str = "", now: datetime | None = None, refresh: bool = True, fetcher=fetch_all) -> dict:
    """One scheduled run: pool -> every user's matches + alerts -> every connected user's Gmail."""
    now = _now(now)
    report: dict = {}
    if refresh:
        try:
            report["pool"] = refresh_pool(store, base, session, now, fetcher)
        except Exception as e:  # noqa: BLE001 - still match against the last pool
            report["pool"] = {"error": type(e).__name__}
    pool = load_pool(store)
    users = profiles(store)
    report["users"] = len(users)
    report["matched"] = report["alerts"] = report["gmail"] = 0
    for uid, p in users.items():
        try:
            m = match_user(store, base, uid, pool, now, p)
            report["matched"] += 1
            if m.get("new"):
                jobs = [j for j in (store.get("matches", uid) or {}).get("jobs", []) if j["id"] in set(m["new"])]
                report["alerts"] += notify_user(store, uid, jobs, vapid_key, vapid_subject, session)
        except Exception as e:  # noqa: BLE001
            log.warning("matching failed for a user: %s", type(e).__name__)
    if oauth:
        for uid in store.list_ids("secrets"):
            if "error" not in gmail_sync_user(store, oauth, session, uid, now):
                report["gmail"] += 1
    return report


def delete_user(store: Store, session, uid: str, delete_auth=None) -> None:
    """Everything stored for this user, then the sign-in account itself."""
    from . import resume_intake
    try:
        gmail_link.disconnect(store, session, uid)  # revokes Google access too
    except Exception:  # noqa: BLE001 - deletion must not stop because Google is unreachable
        store.delete("secrets", uid)
    resume_intake.delete(store, uid)
    for coll in ("users", "profiles", "matches", "gmail", "push", "secrets"):
        store.delete(coll, uid)
    for sid in store.list_ids("oauth_states"):
        if (store.get("oauth_states", sid) or {}).get("uid") == uid:
            store.delete("oauth_states", sid)
    if delete_auth:
        delete_auth(uid)

