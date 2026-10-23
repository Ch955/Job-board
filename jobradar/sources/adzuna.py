"""Adzuna search API (free key): api.adzuna.com/v1/api/jobs/<country>/search/<page>"""

from __future__ import annotations

from ..config import env
from ..models import Job
from .base import SourceContext, SourceSkipped

NAME = "adzuna"
URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def parse(payload: dict) -> list[Job]:
    jobs = []
    for j in payload.get("results", []):
        location = (j.get("location") or {}).get("display_name", "")
        text = f"{j.get('title', '')} {location}".lower()
        jobs.append(
            Job(
                title=j.get("title", ""),
                company=(j.get("company") or {}).get("display_name", "Unknown"),
                location=location,
                url=j.get("redirect_url", ""),
                source=NAME,
                remote="remote" in text or "work from home" in text,
                posted_date=j.get("created"),
                description=j.get("description", ""),
            )
        )
    return jobs


def fetch(ctx: SourceContext) -> list[Job]:
    app_id, app_key = env("ADZUNA_APP_ID"), env("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise SourceSkipped("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
    cfg = ctx.source_cfg
    max_days = ctx.config.get("pipeline", {}).get("max_age_days", 30)
    jobs: list[Job] = []
    for what in cfg.get("queries", ["data engineer"]):
        for where in cfg.get("where", [""]):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what": what,
                "where": where,
                "results_per_page": cfg.get("results_per_page", 50),
                "max_days_old": max_days,
                "sort_by": "date",
                "content-type": "application/json",
            }
            try:
                r = ctx.session.get(URL.format(country=cfg.get("country", "in")), params=params)
                r.raise_for_status()
                jobs += parse(r.json())
            except Exception as e:  # noqa: BLE001 - one query failing shouldn't stop the rest
                ctx.warnings.append(f"{NAME} '{what}' @ {where}: {e.__class__.__name__}")
    return jobs
