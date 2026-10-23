"""LinkedIn, Naukri, Indeed, Glassdoor and Google Jobs via the open-source JobSpy scraper (python-jobspy).

No login and no account: JobSpy reads the public, logged-out search pages.
Each site x query is its own call, so one site rate-limiting us only loses
that slice. Jobs are tagged with the real site name ("linkedin", "naukri"...).
"""

from __future__ import annotations

import logging
import math
from contextlib import contextmanager

from ..models import Job
from .base import SourceContext, SourceSkipped

NAME = "jobspy"
LOGGER_NAMES = {"linkedin": "LinkedIn", "naukri": "Naukri", "indeed": "Indeed",
                "glassdoor": "Glassdoor", "google": "Google"}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.errors.append(record.getMessage())


@contextmanager
def _capture_errors(site: str):
    """JobSpy logs network errors and returns an empty result instead of raising.
    Capture them (and keep them off the console) so a block shows up as a failure."""
    logger = logging.getLogger(f"JobSpy:{LOGGER_NAMES.get(site, site)}")
    capture = _Capture()
    saved = logger.handlers[:]
    logger.handlers = [capture]
    try:
        yield capture
    finally:
        logger.handlers = saved


def _val(record: dict, key: str):
    """DataFrame rows use NaN for missing values; turn those into None."""
    v = record.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def parse(records: list[dict]) -> list[Job]:
    jobs = []
    for r in records:
        site = _val(r, "site") or "jobspy"
        skills = _val(r, "skills")
        if isinstance(skills, (list, tuple)):
            skills = ", ".join(skills)
        description = " ".join(
            str(x) for x in (_val(r, "description"), skills and f"Skills: {skills}",
                             _val(r, "experience_range") and f"Experience: {_val(r, 'experience_range')}") if x
        )
        location = _val(r, "location") or ("India" if site == "naukri" else "")
        posted = _val(r, "date_posted")
        jobs.append(
            Job(
                title=_val(r, "title") or "",
                company=_val(r, "company") or "Unknown",
                location=str(location),
                url=_val(r, "job_url") or _val(r, "job_url_direct") or "",
                apply_url=_val(r, "job_url_direct") or "",
                source=str(site),
                remote=bool(_val(r, "is_remote")),
                posted_date=posted.isoformat() if hasattr(posted, "isoformat") else posted,
                description=description,
            )
        )
    return jobs


def _scrape(ctx: SourceContext, site: str, query: str) -> list[dict]:
    if getattr(ctx.session, "offline", False):
        return ctx.session.jobspy_records(site, query)  # type: ignore[attr-defined]
    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        raise SourceSkipped("python-jobspy not installed (pip install -r requirements.txt)") from e
    cfg = ctx.source_cfg
    with _capture_errors(site) as capture:
        df = scrape_jobs(
            site_name=[site],
            search_term=query,
            # Google Jobs takes one plain search, like typing into Google
            google_search_term=f"{query} jobs in {cfg.get('location', 'India')} since {(cfg.get('hours_old') or 72) // 24 or 1} days",
            location=cfg.get("location", "India"),
            country_indeed=cfg.get("country_indeed", "india"),
            results_wanted=int(cfg.get("results_wanted", 30)),
            hours_old=cfg.get("hours_old"),
            fetch_description=bool(cfg.get("fetch_description", True)),
            description_format="plain",
            verbose=0,
        )
    records = df.to_dict("records") if df is not None and not df.empty else []
    if capture.errors and not records:
        raise RuntimeError(capture.errors[0][:200])
    return records


def fetch(ctx: SourceContext) -> list[Job]:
    cfg = ctx.source_cfg
    jobs: list[Job] = []
    failures = 0
    calls = [(s, q) for s in cfg.get("sites", ["naukri", "linkedin"]) for q in cfg.get("queries", ["data engineer"])]
    for site, query in calls:
        try:
            found = parse(_scrape(ctx, site, query))
            jobs += found
        except SourceSkipped:
            raise
        except Exception as e:  # noqa: BLE001 - e.g. LinkedIn 429; keep going with the other sites
            failures += 1
            ctx.warnings.append(f"{site} '{query}': {type(e).__name__}: {str(e)[:120]}")
    if calls and failures == len(calls):
        raise RuntimeError(f"all {failures} JobSpy calls failed")
    return jobs
