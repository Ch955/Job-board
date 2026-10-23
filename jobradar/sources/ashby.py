"""Ashby public job board API: api.ashbyhq.com/posting-api/job-board/<slug>"""

from __future__ import annotations

from ..models import Job
from .base import SourceContext, fetch_companies

NAME = "ashby"
URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"


def parse(payload: dict, company: str) -> list[Job]:
    jobs = []
    for j in payload.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locations = [j.get("location") or ""]
        locations += [s.get("location", "") for s in j.get("secondaryLocations") or []]
        location = " / ".join(x for x in locations if x)
        workplace = (j.get("workplaceType") or "").lower()
        jobs.append(
            Job(
                title=j.get("title", ""),
                company=company,
                location=location,
                url=j.get("jobUrl") or j.get("applyUrl", ""),
                source=NAME,
                remote=bool(j.get("isRemote")) or workplace == "remote",
                posted_date=j.get("publishedAt"),
                description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
            )
        )
    return jobs


def fetch_one(ctx: SourceContext, company: dict) -> list[Job]:
    r = ctx.session.get(URL.format(slug=company["slug"]))
    r.raise_for_status()
    return parse(r.json(), company["name"])


def fetch(ctx: SourceContext) -> list[Job]:
    return fetch_companies(ctx, NAME, fetch_one)
