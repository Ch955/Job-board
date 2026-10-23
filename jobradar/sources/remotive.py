"""Remotive public API (no key): remotive.com/api/remote-jobs

Remotive asks clients to call at most a few times a day; config throttles it.
"""

from __future__ import annotations

from ..models import Job
from .base import SourceContext

NAME = "remotive"
URL = "https://remotive.com/api/remote-jobs"


def parse(payload: dict) -> list[Job]:
    return [
        Job(
            title=j.get("title", ""),
            company=j.get("company_name", ""),
            location=f"Remote ({j.get('candidate_required_location') or 'Anywhere'})",
            url=j.get("url", ""),
            source=NAME,
            remote=True,
            posted_date=j.get("publication_date"),
            description=j.get("description", ""),
        )
        for j in payload.get("jobs", [])
    ]


def fetch(ctx: SourceContext) -> list[Job]:
    params = {k: v for k, v in (("category", ctx.source_cfg.get("category")),
                                ("search", ctx.source_cfg.get("search"))) if v}
    r = ctx.session.get(URL, params=params)
    r.raise_for_status()
    return parse(r.json())
