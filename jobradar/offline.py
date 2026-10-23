"""Offline mode: serve recorded-shape fixtures instead of hitting the network.

Lets you (and the tests) run the whole pipeline with no internet and no keys.
Fixture dates are written as {{DAYS_AGO:n}} placeholders so they never go stale.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from .config import ROOT

FIXTURES = ROOT / "tests" / "fixtures"

ROUTES = [
    (r"boards-api\.greenhouse\.io/v1/boards/databricks/", "greenhouse_databricks.json"),
    (r"api\.lever\.co/v0/postings/cred\b", "lever_cred.json"),
    (r"api\.ashbyhq\.com/posting-api/job-board/atlan\b", "ashby_atlan.json"),
    (r"api\.smartrecruiters\.com/v1/companies/Visa/postings/sr-1$", "smartrecruiters_visa_detail.json"),
    (r"api\.smartrecruiters\.com/v1/companies/Visa/postings$", "smartrecruiters_visa_list.json"),
    (r"api\.adzuna\.com/", "adzuna.json"),
    (r"jooble\.org/api/", "jooble.json"),
    (r"remotive\.com/api/", "remotive.json"),
    (r"arbeitnow\.com/api/", "arbeitnow.json"),
]

_PLACEHOLDER = re.compile(r'"\{\{(DAYS_AGO|EPOCH_DAYS_AGO|EPOCH_MS_DAYS_AGO):(\d+)\}\}"')


def render_fixture(name: str, now: datetime | None = None) -> object:
    now = now or datetime.now(timezone.utc)
    raw = (FIXTURES / name).read_text(encoding="utf-8")

    def sub(m: re.Match) -> str:
        when = now - timedelta(days=int(m.group(2)))
        kind = m.group(1)
        if kind == "DAYS_AGO":
            return json.dumps(when.isoformat(timespec="seconds"))
        if kind == "EPOCH_DAYS_AGO":
            return str(int(when.timestamp()))
        return str(int(when.timestamp() * 1000))

    # NaN mimics the missing values a pandas DataFrame hands back
    return json.loads(_PLACEHOLDER.sub(sub, raw), parse_constant=lambda c: float(c))


class FakeResponse:
    def __init__(self, url: str, status_code: int, payload: object = None):
        self.url, self.status_code, self._payload = url, status_code, payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}", response=self)  # type: ignore[arg-type]


class OfflineSession:
    """Duck-typed stand-in for requests.Session. Unknown URLs return 404."""

    offline = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def jobspy_records(self, site: str, query: str) -> list[dict]:
        """JobSpy doesn't use our session; its sources ask for fixture rows instead."""
        self.calls.append(f"JOBSPY {site} {query}")
        return [r for r in render_fixture("jobspy.json") if r["site"] == site]

    def request(self, method: str, url: str, **_: object) -> FakeResponse:
        self.calls.append(f"{method} {url}")
        for pattern, fixture in ROUTES:
            if re.search(pattern, url):
                return FakeResponse(url, 200, render_fixture(fixture))
        return FakeResponse(url, 404)

    def get(self, url: str, **kw: object) -> FakeResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: object) -> FakeResponse:
        return self.request("POST", url, **kw)


def enable_fake_keys() -> None:
    """So key-gated sources run against fixtures. Only affects this process."""
    for name in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY"):
        os.environ.setdefault(name, "offline")
