"""Scheduled work for every user: refresh the shared pool, score it per user,
sync each user's own Gmail, and alert each user about their own new matches.

Each user is handled in isolation: one user's failure (revoked Gmail, bad data)
is recorded on their status and never stops or leaks into another user's run.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone

from .. import gmail, gmail_status, push
from ..models import Job
from ..pipeline import dedupe, fetch_all
from ..scoring import Scorer, age_days
from . import gmail_link
from .matching import UserMatcher, _role_patterns
from .profile import is_complete
from .store import Conflict, Store

log = logging.getLogger("jobradar.tasks")
POOL_CHUNK = 120          # jobs per pool document (Firestore docs are capped at 1 MiB)
DESC_CHARS = 2500         # enough of the description for skill matching
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
def pool_config(base: dict, users: dict[str, dict]) -> dict:
    """Search for every user's target roles; keep any posting at least one user could want."""
    roles = []
    for p in users.values():
        for r in p.get("target_roles") or [p.get("current_role", "")]:
            if r and r.lower() not in (x.lower() for x in roles):
                roles.append(r)
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


def refresh_pool(store: Store, base: dict, session, now: datetime | None = None, fetcher=fetch_all) -> dict:
    now = _now(now)
    users = profiles(store)
    cfg = pool_config(base, users)
    meta = store.get("pool", "meta") or {}
    fetch_state = meta.get("fetch_state") or {}
    jobs, runs = fetcher(cfg, session, fetch_state, now=now)
    scorer = Scorer(cfg)
    kept = []
    for job in dedupe(jobs):
        job.first_seen = job.first_seen or _iso(now)
        if not scorer.is_relevant(job) or (age_days(job, now) or 0) > 30 or not scorer.location_points(job)[2]:
            continue
        d = job.to_dict()
        d["description"] = (job.description or "")[:DESC_CHARS]
        kept.append(d)
    # Keep last run's jobs that are still recent, so a source outage doesn't empty everyone's list
    have = {d["id"] for d in kept}
    for old in load_pool(store):
        if old.id not in have and (age_days(old, now) or 0) <= 30:
            d = old.to_dict()
            d["description"] = old.description[:DESC_CHARS]
            kept.append(d)
    chunks = [kept[i:i + POOL_CHUNK] for i in range(0, len(kept), POOL_CHUNK)] or [[]]
    for i, c in enumerate(chunks):
        store.set("pool", f"c{i}", {"jobs": c})
    for i in range(len(chunks), int(meta.get("chunks", 0))):
        store.delete("pool", f"c{i}")
    store.set("pool", "meta", {"chunks": len(chunks), "count": len(kept), "updated": _iso(now),
                               "fetch_state": fetch_state, "sources": runs})
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
    jobs = UserMatcher(base, p).match(pool, now)[:MAX_MATCHES]
    old = store.get("matches", uid) or {}
    seen = set(old.get("seen") or [])
    new = [j for j in jobs if j["id"] not in seen]
    for j in jobs:
        j.pop("description", None)
    store.set("matches", uid, {"jobs": jobs, "threshold": p.get("threshold", 70), "updated": _iso(now),
                               "count": len(jobs), "seen": sorted(seen | {j["id"] for j in jobs})[-3000:]})
    return {"count": len(jobs), "new": [j["id"] for j in new]}


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
        status.update(last_success=_iso(now), last_error=None, last_report=report)
        store.update("gmail", uid, status)
        return report
    except gmail.GmailAuthError as e:
        if not e.revoked:
            status["last_error"] = "Gmail sign-in failed; will retry."
            store.update("gmail", uid, status)
        return {"error": "auth"}
    except Exception as e:  # noqa: BLE001 - isolate users from each other
        log.warning("gmail sync failed for a user: %s", type(e).__name__)
        status["last_error"] = "Gmail sync failed; will retry." + (f" ({e.status})" if isinstance(e, gmail.GmailApiError) else "")
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

