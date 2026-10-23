"""SmartRecruiters public postings API: api.smartrecruiters.com/v1/companies/<id>/postings

The list endpoint has no description, so we fetch details only for postings
whose title passes the relevance gate (capped per company).
"""

from __future__ import annotations

from ..models import Job
from ..scoring import Scorer
from .base import SourceContext, fetch_companies

NAME = "smartrecruiters"
LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}"
PUBLIC_URL = "https://jobs.smartrecruiters.com/{slug}/{id}"


COUNTRIES = {"in": "India", "us": "USA", "gb": "UK", "de": "Germany", "sg": "Singapore"}


def _location(loc: dict) -> str:
    if loc.get("fullLocation"):
        return loc["fullLocation"]
    country = (loc.get("country") or "").lower()
    country = COUNTRIES.get(country, country.upper())
    return ", ".join(x for x in (loc.get("city"), loc.get("region"), country) if x)


def parse_listing(item: dict, slug: str, company: str) -> Job:
    loc = item.get("location") or {}
    return Job(
        title=item.get("name", ""),
        company=company,
        location=_location(loc),
        url=PUBLIC_URL.format(slug=slug, id=item.get("id", "")),
        source=NAME,
        remote=bool(loc.get("remote")),
        posted_date=item.get("releasedDate"),
    )


def parse_detail(detail: dict) -> str:
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    return " ".join((sections.get(k) or {}).get("text", "") for k in ("jobDescription", "qualifications"))


def fetch_one(ctx: SourceContext, company: dict) -> list[Job]:
    slug, name = company["slug"], company["name"]
    params = {"limit": 100, "offset": 0, "q": ctx.source_cfg.get("query", "data")}
    items: list[dict] = []
    while True:
        r = ctx.session.get(LIST_URL.format(slug=slug), params=params)
        r.raise_for_status()
        data = r.json()
        items += data.get("content", [])
        params["offset"] += params["limit"]
        if params["offset"] >= min(data.get("totalFound", 0), 500):
            break

    scorer = Scorer(ctx.config)
    budget = int(ctx.source_cfg.get("max_details_per_company", 15))
    jobs = []
    for item in items:
        job = parse_listing(item, slug, name)
        if not scorer.is_relevant(job):
            continue
        if budget > 0:
            budget -= 1
            try:
                d = ctx.session.get(DETAIL_URL.format(slug=slug, id=item["id"]))
                d.raise_for_status()
                job.description = parse_detail(d.json())
                job.__post_init__()  # re-clean the description
            except Exception as e:  # noqa: BLE001 - listing is still useful without details
                ctx.warnings.append(f"{NAME}/{slug}: detail {item.get('id')}: {e}")
        jobs.append(job)
    return jobs


def fetch(ctx: SourceContext) -> list[Job]:
    return fetch_companies(ctx, NAME, fetch_one)
