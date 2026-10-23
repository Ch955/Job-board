"""Jooble REST API (free key): POST jooble.org/api/<key>"""

from __future__ import annotations

from ..config import env
from ..models import Job
from .base import SourceContext, SourceSkipped

NAME = "jooble"
URL = "https://jooble.org/api/{key}"


def parse(payload: dict) -> list[Job]:
    jobs = []
    for j in payload.get("jobs", []):
        location = j.get("location", "")
        jobs.append(
            Job(
                title=j.get("title", ""),
                company=j.get("company") or "Unknown",
                location=location,
                url=j.get("link", ""),
                source=NAME,
                remote="remote" in f"{location} {j.get('type', '')}".lower(),
                posted_date=j.get("updated"),
                description=j.get("snippet", ""),
            )
        )
    return jobs


def fetch(ctx: SourceContext) -> list[Job]:
    key = env("JOOBLE_API_KEY")
    if not key:
        raise SourceSkipped("JOOBLE_API_KEY not set")
    cfg = ctx.source_cfg
    jobs: list[Job] = []
    for keywords in cfg.get("queries", ["data engineer"]):
        for location in cfg.get("locations", ["India"]):
            try:
                r = ctx.session.post(
                    URL.format(key=key), json={"keywords": keywords, "location": location, "page": "1"}
                )
                r.raise_for_status()
                jobs += parse(r.json())
            except Exception as e:  # noqa: BLE001 - never log the URL: it contains the key
                ctx.warnings.append(f"{NAME} '{keywords}' @ {location}: {e.__class__.__name__}")
    return jobs
