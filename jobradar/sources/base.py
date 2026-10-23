"""Plumbing shared by all sources."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import requests

from ..models import Job

log = logging.getLogger("jobradar.sources")


class SourceSkipped(Exception):
    """Raised when a source can't run (e.g. missing API key). Not an error."""


@dataclass
class SourceContext:
    config: dict
    source_cfg: dict
    session: requests.Session
    warnings: list[str] = field(default_factory=list)

    @property
    def max_workers(self) -> int:
        return int(self.config.get("pipeline", {}).get("max_workers", 8))

    def companies(self, ats: str) -> list[dict]:
        return self.config.get("companies", {}).get(ats) or []


def fetch_companies(
    ctx: SourceContext, ats: str, fetch_one: Callable[[SourceContext, dict], list[Job]]
) -> list[Job]:
    """Fetch every company board for one ATS in parallel; a bad slug only logs a warning."""
    companies = ctx.companies(ats)

    def run(company: dict) -> list[Job]:
        try:
            return fetch_one(ctx, company)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            ctx.warnings.append(f"{ats}/{company['slug']}: HTTP {code}")
        except Exception as e:  # noqa: BLE001 - isolate every company
            ctx.warnings.append(f"{ats}/{company['slug']}: {type(e).__name__}: {e}")
        return []

    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=ctx.max_workers) as pool:
        for result in pool.map(run, companies):
            jobs.extend(result)
    return jobs
