"""fetch -> gate -> dedupe -> score -> filter -> merge with history -> save."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import store
from .config import ROOT
from .http import make_session
from .models import Job
from .scoring import Scorer, age_days
from .sources import REGISTRY
from .sources.base import SourceContext, SourceSkipped

log = logging.getLogger("jobradar")

# Earlier sources win on duplicates: a direct company board link beats an aggregator.
SOURCE_PRIORITY = {name: i for i, name in enumerate(REGISTRY)}
# JobSpy tags jobs with the real site name
for _i, _site in enumerate(("naukri", "linkedin", "indeed", "glassdoor", "google")):
    SOURCE_PRIORITY[_site] = SOURCE_PRIORITY["jobspy"] + _i / 10


@dataclass
class RunResult:
    jobs: list[Job]
    new_jobs: list[Job]
    source_runs: dict
    dropped: dict = field(default_factory=dict)
    output: Path | None = None


def _due(name: str, cfg: dict, state: dict, now: datetime) -> bool:
    hours = cfg.get("min_interval_hours")
    last = state.get("last_fetched", {}).get(name)
    if not hours or not last:
        return True
    return now - datetime.fromisoformat(last) >= timedelta(hours=float(hours)) - timedelta(minutes=10)


def fetch_all(
    config: dict,
    session: requests.Session,
    state: dict,
    only: set[str] | None = None,
    now: datetime | None = None,
    ignore_throttle: bool = False,
) -> tuple[list[Job], dict]:
    """Run every enabled source in isolation. Returns (jobs, per-source run report)."""
    now = now or datetime.now(timezone.utc)
    jobs: list[Job] = []
    runs: dict = {}
    for name, module in REGISTRY.items():
        cfg = config.get("sources", {}).get(name, {}) or {}
        if only and name not in only:
            continue
        if not cfg.get("enabled", True):
            runs[name] = {"status": "disabled", "count": 0}
            continue
        if not ignore_throttle and not _due(name, cfg, state, now):
            runs[name] = {"status": "throttled", "count": 0}
            continue
        ctx = SourceContext(config=config, source_cfg=cfg, session=session)
        started = datetime.now(timezone.utc)
        try:
            found = module.fetch(ctx)
            jobs.extend(found)
            runs[name] = {"status": "ok", "count": len(found)}
            state.setdefault("last_fetched", {})[name] = now.isoformat(timespec="seconds")
        except SourceSkipped as e:
            runs[name] = {"status": "skipped", "count": 0, "reason": str(e)}
        except Exception as e:  # noqa: BLE001 - a broken source must never stop the run
            runs[name] = {"status": "error", "count": 0, "error": f"{type(e).__name__}: {e}"[:300]}
        runs[name]["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        if ctx.warnings:
            runs[name]["warnings"] = ctx.warnings[:25]
            runs[name]["warning_count"] = len(ctx.warnings)
        log.info("%-16s %s", name, runs[name])
    return jobs, runs


def dedupe(jobs: list[Job]) -> list[Job]:
    """One job per company+title+location; prefer higher-priority source, then richer data."""
    best: dict[str, Job] = {}
    for job in jobs:
        cur = best.get(job.id)
        if cur is None:
            best[job.id] = job
            continue
        rank_new = (SOURCE_PRIORITY.get(job.source, 99), -len(job.description))
        rank_cur = (SOURCE_PRIORITY.get(cur.source, 99), -len(cur.description))
        if rank_new < rank_cur:
            best[job.id] = job
    return list(best.values())


def process(
    fetched: list[Job],
    previous: dict[str, Job],
    config: dict,
    now: datetime | None = None,
) -> tuple[list[Job], list[Job], dict]:
    """Pure function: (fetched, previous) -> (final jobs, new jobs, drop counts)."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    p = config.get("pipeline", {})
    min_score = int(p.get("min_score", 40))
    max_age = int(p.get("max_age_days", 30))
    keep_statuses = set(p.get("keep_statuses", []))
    snippet_chars = int(p.get("snippet_chars", 300))
    drop_ineligible = config.get("location", {}).get("drop_ineligible", True)
    scorer = Scorer(config)
    dropped = {"irrelevant_title": 0, "location": 0, "too_old": 0, "low_score": 0, "duplicates": 0}

    relevant = []
    for job in fetched:
        if scorer.is_relevant(job):
            relevant.append(job)
        else:
            dropped["irrelevant_title"] += 1
    unique = dedupe(relevant)
    dropped["duplicates"] = len(relevant) - len(unique)

    final: dict[str, Job] = {}
    new_jobs: list[Job] = []
    for job in unique:
        old = previous.get(job.id)
        job.first_seen = old.first_seen if old and old.first_seen else now_iso
        job.status = old.status if old else "new"
        if not job.description_snippet:
            job.description_snippet = job.description[:snippet_chars].rstrip()
        scorer.score(job, now)
        keep = job.status in keep_statuses
        if not keep:
            if drop_ineligible and not job.score_breakdown["location_eligible"]:
                dropped["location"] += 1
                continue
            if (age_days(job, now) or 0) > max_age:
                dropped["too_old"] += 1
                continue
            if job.score < min_score:
                dropped["low_score"] += 1
                continue
        final[job.id] = job
        if old is None:
            new_jobs.append(job)

    # Carry over jobs not seen this run (throttled source, flaky board) until they age out.
    for jid, old in previous.items():
        if jid in final:
            continue
        keep = old.status in keep_statuses
        new_rec, days = scorer.recency_points(old, now)
        if not keep and (days or 0) > max_age:
            continue
        # Re-check location so a rule change (e.g. India-only) cleans up old entries too
        if not keep and drop_ineligible and not scorer.location_points(old)[2]:
            continue
        bd = old.score_breakdown or {}
        old.score = max(0, min(100, old.score - int(bd.get("recency", 0)) + new_rec))
        bd.update(recency=new_rec, age_days=days)
        old.score_breakdown = bd
        if keep or old.score >= min_score:
            final[jid] = old

    # Highest score first; newest first among equal scores
    jobs = sorted(final.values(), key=lambda j: j.posted_date or j.first_seen or "", reverse=True)
    jobs.sort(key=lambda j: -j.score)
    new_jobs.sort(key=lambda j: -j.score)
    return jobs, new_jobs, dropped


def run(
    config: dict,
    only: set[str] | None = None,
    session: requests.Session | None = None,
    output: Path | None = None,
    write: bool = True,
    ignore_throttle: bool = False,
) -> RunResult:
    now = datetime.now(timezone.utc)
    p = config.get("pipeline", {})
    output = output or ROOT / p.get("output", "docs/jobs.json")
    data = store.load(output)
    previous = store.existing_jobs(data)
    state = data.get("state", {})
    session = session or make_session(float(p.get("http_timeout_s", 20)))

    fetched, runs = fetch_all(config, session, state, only, now, ignore_throttle)
    jobs, new_jobs, dropped = process(fetched, previous, config, now)

    if write:
        by_source: dict[str, int] = {}
        for j in jobs:
            by_source[j.source] = by_source.get(j.source, 0) + 1
        store.save(
            output,
            {
                "version": store.SCHEMA_VERSION,
                "generated_at": now.isoformat(timespec="seconds"),
                "profile": {
                    "name": config.get("profile", {}).get("name"),
                    "role": config.get("profile", {}).get("role"),
                },
                "stats": {
                    "total": len(jobs),
                    "new_this_run": len(new_jobs),
                    "fetched_raw": len(fetched),
                    "by_source": by_source,
                    "dropped": dropped,
                    "source_runs": runs,
                },
                "state": state,
                "jobs": [j.to_dict() for j in jobs],
            },
        )
    return RunResult(jobs=jobs, new_jobs=new_jobs, source_runs=runs, dropped=dropped, output=output if write else None)
