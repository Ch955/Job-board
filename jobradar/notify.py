"""Phone notifications for the Job Radar app (Web Push).

Secrets (env / GitHub Secrets):
  VAPID_PRIVATE_KEY      from `python -m jobradar push-keys`
  WEBPUSH_SUBSCRIPTIONS  copied from the app's "Enable alerts" screen. One JSON
                         object per device, one per line (or a JSON array).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import requests

from . import push
from .config import env
from .models import Job

log = logging.getLogger("jobradar.notify")


def load_subscriptions(raw: str | None = None) -> list[dict]:
    raw = (raw if raw is not None else env("WEBPUSH_SUBSCRIPTIONS")) or ""
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        items = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return [s for s in items if isinstance(s, dict) and s.get("endpoint") and s.get("keys")]


def configured() -> tuple[str | None, list[dict], str | None]:
    """Return (private_key, subscriptions, reason_if_not_configured)."""
    key = env("VAPID_PRIVATE_KEY")
    if not key:
        return None, [], "VAPID_PRIVATE_KEY not set"
    try:
        subs = load_subscriptions()
    except json.JSONDecodeError as e:
        return key, [], f"WEBPUSH_SUBSCRIPTIONS is not valid JSON: {e}"
    if not subs:
        return key, [], "WEBPUSH_SUBSCRIPTIONS not set (enable alerts in the app first)"
    return key, subs, None


# ----------------------------------------------------------------- payloads
def job_payload(job: Job) -> dict:
    keywords = ", ".join(job.matched_keywords[:5])
    loc = job.score_breakdown.get("location_label") or job.location
    return {
        "title": f"{job.score} · {job.title}"[:120],
        "body": f"{job.company} · {loc}" + (f"\n{keywords}" if keywords else ""),
        "tag": f"job-{job.id}",
        "job_id": job.id,
        "url": job.url,
    }


def alert_payloads(new_jobs: list[Job], alert_score: int, max_individual: int = 5) -> list[dict]:
    """One notification per strong new job, then a single roll-up so a big first run can't spam you."""
    strong = sorted((j for j in new_jobs if j.score >= alert_score), key=lambda j: -j.score)
    payloads = [job_payload(j) for j in strong[:max_individual]]
    rest = strong[max_individual:]
    if rest:
        payloads.append({
            "title": f"+{len(rest)} more strong matches",
            "body": ", ".join(f"{j.title} @ {j.company}" for j in rest[:4])[:200],
            "tag": "job-rollup",
        })
    return payloads


def summary_payload(jobs: list[dict], alert_score: int, now: datetime | None = None, hours: int = 24) -> dict | None:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    fresh = []
    for j in jobs:
        try:
            if j.get("first_seen") and datetime.fromisoformat(j["first_seen"]) >= since:
                fresh.append(j)
        except ValueError:
            continue
    fresh.sort(key=lambda j: -j.get("score", 0))
    strong = [j for j in fresh if j.get("score", 0) >= alert_score]
    if not fresh:
        body = f"No new matches in the last {hours}h. {len(jobs)} jobs in your list."
    else:
        top = "\n".join(f"{j['score']} · {j['title']} @ {j['company']}" for j in fresh[:3])
        body = f"{len(strong)} scored {alert_score}+.\n{top}"
    return {
        "title": f"Job Radar · {len(fresh)} new job{'s' if len(fresh) != 1 else ''} today",
        "body": body[:300],
        "tag": "daily-summary",
    }


# --------------------------------------------------------------------- send
def send_payloads(payloads: list[dict], subject: str, session: requests.Session | None = None) -> dict:
    """Send every payload to every subscribed device. Never raises: failures are reported."""
    key, subs, reason = configured()
    report = {"sent": 0, "failed": 0, "gone": 0, "skipped": reason}
    if reason or not payloads:
        if reason:
            log.warning("push: %s, skipping", reason)
        return report
    session = session or requests.Session()
    for sub in subs:
        for payload in payloads:
            try:
                push.send(sub, payload, key, subject, session)
                report["sent"] += 1
            except push.PushGone:
                report["gone"] += 1
                log.warning("push: a device's subscription expired. Re-enable alerts in the app "
                            "and update WEBPUSH_SUBSCRIPTIONS.")
                break
            except Exception as e:  # noqa: BLE001 - one bad device mustn't block the others
                report["failed"] += 1
                log.warning("push: send failed: %s", type(e).__name__)
    return report


def subject_for(config: dict) -> str:
    email = config.get("profile", {}).get("apply_email") or "noreply@example.com"
    return f"mailto:{email}"
