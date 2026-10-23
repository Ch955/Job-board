"""Lever public postings API: api.lever.co/v0/postings/<slug>?mode=json"""

from __future__ import annotations

from ..models import Job
from .base import SourceContext, fetch_companies

NAME = "lever"
URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


def parse(payload: list, company: str) -> list[Job]:
    jobs = []
    for j in payload or []:
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        workplace = (j.get("workplaceType") or "").lower()
        location = " / ".join(locations)
        lists = " ".join(f"{x.get('text', '')} {x.get('content', '')}" for x in j.get("lists") or [])
        jobs.append(
            Job(
                title=j.get("text", ""),
                company=company,
                location=location,
                url=j.get("hostedUrl") or j.get("applyUrl", ""),
                source=NAME,
                remote=workplace == "remote" or "remote" in location.lower(),
                posted_date=j.get("createdAt"),
                description=" ".join(
                    filter(None, [j.get("descriptionPlain"), lists, j.get("additionalPlain")])
                ),
            )
        )
    return jobs


def fetch_one(ctx: SourceContext, company: dict) -> list[Job]:
    r = ctx.session.get(URL.format(slug=company["slug"]))
    r.raise_for_status()
    return parse(r.json(), company["name"])


def fetch(ctx: SourceContext) -> list[Job]:
    return fetch_companies(ctx, NAME, fetch_one)
