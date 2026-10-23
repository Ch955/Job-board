import json
from datetime import datetime, timedelta, timezone

import pytest

from jobradar import pipeline
from jobradar.models import Job
from jobradar.offline import OfflineSession, enable_fake_keys
from jobradar.sources import REGISTRY


@pytest.fixture
def out(tmp_path):
    enable_fake_keys()
    return tmp_path / "jobs.json"


def run(config, out, **kw):
    return pipeline.run(config, session=OfflineSession(), output=out, ignore_throttle=True, **kw)


def test_full_offline_run(config, out):
    result = run(config, out)
    data = json.loads(out.read_text())
    titles = {j["title"] for j in data["jobs"]}

    assert "Senior Data Engineer" in titles           # greenhouse, kept
    assert "Account Executive, Enterprise" not in titles  # title gate
    assert "Staff Data Engineer" not in titles         # San Francisco, not eligible
    assert "Data Engineering Intern" not in titles     # excluded
    companies = {j["company"] for j in data["jobs"]}
    assert "Global Remote Co" not in companies         # remote worldwide, not India
    assert "Example UK" not in companies               # LinkedIn job in London
    assert {"Example Tech Services", "Example Retail", "Example SaaS", "Example Bank"} <= companies
    assert {"naukri", "linkedin", "indeed"} <= {j["source"] for j in data["jobs"]}
    assert all(j["score"] >= 40 for j in data["jobs"])
    assert [j["score"] for j in data["jobs"]] == sorted((j["score"] for j in data["jobs"]), reverse=True)

    # The Adzuna copy of the Databricks job deduped into the direct Greenhouse one
    dbx = [j for j in data["jobs"] if j["company"].startswith("Databricks")]
    assert len(dbx) == 1 and dbx[0]["source"] == "greenhouse"

    # Old jooble job (45 days) dropped
    assert "Old Corp" not in {j["company"] for j in data["jobs"]}

    # Schema
    required = {"id", "title", "company", "location", "remote", "source", "url", "posted_date",
                "description_snippet", "score", "status", "matched_keywords"}
    assert required <= set(data["jobs"][0])
    assert len(result.new_jobs) == len(data["jobs"])
    assert data["stats"]["source_runs"]["greenhouse"]["status"] == "ok"


def test_second_run_preserves_status_and_first_seen(config, out):
    run(config, out)
    data = json.loads(out.read_text())
    data["jobs"][0]["status"] = "applied"
    data["jobs"][0]["first_seen"] = "2026-01-01T00:00:00+00:00"
    out.write_text(json.dumps(data))

    result = run(config, out)
    again = {j.id: j for j in result.jobs}
    first = again[data["jobs"][0]["id"]]
    assert first.status == "applied" and first.first_seen == "2026-01-01T00:00:00+00:00"
    assert result.new_jobs == []


def test_failing_source_does_not_stop_others(config, out, monkeypatch):
    def boom(ctx):
        raise RuntimeError("API down")

    monkeypatch.setattr(REGISTRY["greenhouse"], "fetch", boom)
    result = run(config, out)
    assert result.source_runs["greenhouse"]["status"] == "error"
    assert result.source_runs["lever"]["status"] == "ok"
    assert any(j.source == "lever" for j in result.jobs)


def test_missing_key_skips_source(config, out, monkeypatch):
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    result = run(config, out)
    assert result.source_runs["jooble"]["status"] == "skipped"


def test_throttle(config):
    now = datetime.now(timezone.utc)
    state = {"last_fetched": {"remotive": (now - timedelta(hours=2)).isoformat()}}
    assert not pipeline._due("remotive", {"min_interval_hours": 6}, state, now)
    assert pipeline._due("remotive", {"min_interval_hours": 6}, state, now + timedelta(hours=5))
    assert pipeline._due("greenhouse", {}, state, now)


def test_carry_over_and_age_out(config):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    def old_job(title, days, status="new"):
        j = Job(title=title, company="C", location="Pune", url="u", source="remotive",
                posted_date=(now - timedelta(days=days)).isoformat(), status=status)
        j.score, j.score_breakdown = 80, {"recency": 10}
        return j
    previous = {j.id: j for j in (old_job("Data Engineer", 2), old_job("ETL Developer", 40),
                                   old_job("Big Data Engineer", 40, status="applied"))}
    jobs, new, _ = pipeline.process([], previous, config, now)
    titles = {j.title for j in jobs}
    assert titles == {"Data Engineer", "Big Data Engineer"}  # 40-day-old applied job kept
    assert new == []
    assert next(j for j in jobs if j.title == "Data Engineer").score == 78  # recency 10 -> 8


def test_carry_over_drops_jobs_abroad_after_rule_change(config):
    """Old foreign jobs already in jobs.json are cleaned out on the next run."""
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    def old(location, status="new"):
        j = Job(title="Data Engineer", company=location, location=location, url="u", source="remotive",
                posted_date=(now - timedelta(days=1)).isoformat(), status=status)
        j.score, j.score_breakdown = 80, {"recency": 10}
        return j
    previous = {j.id: j for j in (old("Remote (Worldwide)"), old("Berlin"), old("Pune"), old("London", "applied"))}
    jobs, _, _ = pipeline.process([], previous, config, now)
    assert {j.location for j in jobs} == {"Pune", "London"}  # applied jobs are never removed
