"""Greenhouse public job board API: boards-api.greenhouse.io/v1/boards/<slug>/jobs"""

from __future__ import annotations

from ..models import Job
from .base import SourceContext, fetch_companies

NAME = "greenhouse"
URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def parse(payload: dict, company: str) -> list[Job]:
    jobs = []
    for j in payload.get("jobs", []):
        location = (j.get("location") or {}).get("name", "")
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [] if o.get("name"))
        jobs.append(
            Job(
                title=j.get("title", ""),
                company=company,
                location=location or offices,
                url=j.get("absolute_url", ""),
                source=NAME,
                remote="remote" in f"{location} {offices}".lower(),
                posted_date=j.get("first_published") or j.get("updated_at"),
                description=j.get("content", ""),
            )
        )
    return jobs


def fetch_one(ctx: SourceContext, company: dict) -> list[Job]:
    r = ctx.session.get(URL.format(slug=company["slug"]))
    r.raise_for_status()
    return parse(r.json(), company["name"])


def fetch(ctx: SourceContext) -> list[Job]:
    return fetch_companies(ctx, NAME, fetch_one)
