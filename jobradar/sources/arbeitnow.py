"""Arbeitnow public job board API (no key): arbeitnow.com/api/job-board-api"""

from __future__ import annotations

from ..models import Job
from .base import SourceContext

NAME = "arbeitnow"
URL = "https://www.arbeitnow.com/api/job-board-api"


def parse(payload: dict, remote_only: bool = True) -> list[Job]:
    jobs = []
    for j in payload.get("data", []):
        if remote_only and not j.get("remote"):
            continue
        location = j.get("location", "")
        jobs.append(
            Job(
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                location=f"Remote ({location})" if j.get("remote") and location else location,
                url=j.get("url", ""),
                source=NAME,
                remote=bool(j.get("remote")),
                posted_date=j.get("created_at"),
                description=f"{j.get('description', '')} {' '.join(j.get('tags') or [])}",
            )
        )
    return jobs


def fetch(ctx: SourceContext) -> list[Job]:
    remote_only = ctx.source_cfg.get("remote_only", True)
    jobs: list[Job] = []
    for page in range(1, int(ctx.source_cfg.get("pages", 3)) + 1):
        r = ctx.session.get(URL, params={"page": page})
        r.raise_for_status()
        payload = r.json()
        jobs += parse(payload, remote_only)
        if not (payload.get("links") or {}).get("next"):
            break
    return jobs
