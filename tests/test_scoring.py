from datetime import datetime, timedelta, timezone

import pytest

from jobradar.models import Job
from jobradar.scoring import Scorer

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def job(title="Data Engineer", location="Hyderabad", description="", days_ago=0, remote=False):
    return Job(title=title, company="Acme", location=location, url="u", source="test", remote=remote,
               description=description, posted_date=(NOW - timedelta(days=days_ago)).isoformat())


@pytest.fixture
def scorer(config):
    return Scorer(config)


@pytest.mark.parametrize("title", [
    "Data Engineer", "Senior Azure Data Engineer", "Big Data Developer", "ETL Developer",
    "Databricks Engineer", "Software Engineer - Data Platform", "Analytics Engineer",
])
def test_relevant_titles(scorer, title):
    assert scorer.is_relevant(job(title))


@pytest.mark.parametrize("title", [
    "Account Executive", "Data Engineering Intern", "Engineering Manager, Data", "Data Scientist",
    "Director of Data Engineering", "Frontend Engineer",
])
def test_irrelevant_titles(scorer, title):
    assert not scorer.is_relevant(job(title))


@pytest.mark.parametrize("location,remote,label,eligible", [
    ("Hyderabad, Telangana", False, "Hyderabad", True),
    ("Bengaluru, India", False, "Bangalore", True),
    ("Navi Mumbai", False, "Mumbai", True),
    ("Chennai, India", False, "India", True),
    ("Coimbatore, Tamil Nadu", False, "India", True),
    ("Gurugram, Haryana", False, "India", True),
    ("Kochi, IN", False, "India", True),
    ("Remote - India", True, "Remote (India)", True),
    ("Bangalore / San Francisco", False, "Bangalore", True),
    # Everything abroad, and remote that doesn't say India, is out
    ("Remote (Worldwide)", True, "Remote (not India)", False),
    ("Remote", True, "Remote (not India)", False),
    ("Remote - EMEA", True, "Remote (not India)", False),
    ("Remote (USA Only)", True, "Remote (not India)", False),
    ("London, UK", False, "Abroad", False),
    ("Singapore", False, "Abroad", False),
    ("Warsaw, Poland", False, "Abroad", False),
    ("Hyderabad, Sindh, Pakistan", False, "Abroad", False),
])
def test_location(scorer, location, remote, label, eligible):
    """India only: any Indian city, or remote that says India."""
    _, got_label, got_eligible = scorer.location_points(job(location=location, remote=remote))
    assert (got_label, got_eligible) == (label, eligible)


@pytest.mark.parametrize("text,expected", [
    ("3+ years of experience in Spark", (3, None)),
    ("Experience: 2-5 years", (2, 5)),
    ("5 to 8 yrs experience", (5, 8)),
    ("Experience: 3-6 Yrs", (3, 6)),          # Naukri's format via JobSpy
    ("minimum of 7 years of relevant experience", (7, None)),
    ("5+ years of experience overall, 2+ years of experience with Databricks", (5, None)),  # largest wins
    ("We have 20 years of experience serving clients. 3+ years experience required.", (3, None)),
    ("We were founded 12 years ago", (None, None)),   # no 'experience' nearby
    ("No numbers here", (None, None)),
])
def test_required_range(text, expected):
    assert Scorer.required_range(text) == expected


@pytest.mark.parametrize("text,eligible", [
    ("2+ years of experience", True),
    ("3-5 years of experience", True),
    ("4+ years experience", True),
    ("Experience: 1-3 years", True),     # overlaps 2-4
    ("4-7 years of experience", True),   # you meet the minimum
    ("5+ years of experience", False),
    ("6-8 years experience", False),
    ("Experience: 0-1 years", False),    # fresher role
    ("no experience stated", True),
])
def test_experience_band_2_to_4(scorer, text, eligible):
    points, _, _, ok = scorer.experience_points(text)
    assert ok is eligible
    assert points == (10 if eligible and "years" in text else 5 if eligible else 0)


def test_recency(scorer):
    assert scorer.recency_points(job(days_ago=0), NOW)[0] == 10
    assert scorer.recency_points(job(days_ago=5), NOW)[0] == 6
    assert scorer.recency_points(job(days_ago=40), NOW)[0] == 0


def test_perfect_match_scores_high_and_lists_keywords(scorer):
    j = scorer.score(job(
        "Azure Data Engineer", "Hyderabad",
        "ADF, Azure Data Factory, Databricks, PySpark, ADLS Gen2, Snowflake, SQL, Python. 3+ years of experience.",
    ), NOW)
    assert j.score >= 90
    assert {"ADF", "Databricks", "PySpark", "ADLS Gen2", "Snowflake"} <= set(j.matched_keywords)
    assert j.score_breakdown["location_label"] == "Hyderabad"


def test_all_indian_cities_score_the_same_as_preferred(scorer):
    hyd = scorer.location_points(job(location="Hyderabad"))[0]
    assert scorer.location_points(job(location="Jaipur, Rajasthan"))[0] == hyd


def test_skill_points_are_capped(scorer):
    pts, matched = scorer.skill_points(" ".join(k[0] for k in scorer.skills) + " azure data factory databricks pyspark")
    assert pts == scorer.skill_max
    assert len(matched) > 5


def test_senior_titles_penalized(scorer):
    base = scorer.score(job("Data Engineer", description="Spark"), NOW).score
    staff = scorer.score(job("Staff Data Engineer", description="Spark"), NOW).score
    assert staff == base - 15


def test_score_is_clamped(scorer):
    j = scorer.score(job("Principal Data Engineer", "Toronto", "", days_ago=60), NOW)
    assert 0 <= j.score <= 100


def test_resume_skills_score_high(scorer):
    """Text lifted from a typical Azure DE posting that matches the resume."""
    pts, matched = scorer.skill_points(
        "Build metadata-driven ADF pipelines into ADLS Gen2, SCD Type 2 in Databricks, incremental loads, "
        "Self-Hosted Integration Runtime for on-prem sources, Azure SQL, Key Vault, Azure DevOps CI/CD.")
    assert pts == scorer.skill_max
    assert {"ADF", "SCD", "Incremental Load", "Metadata-driven", "SHIR / Hybrid", "Azure Security"} <= set(matched)


def test_non_resume_tools_only_get_token_points(scorer):
    pts, matched = scorer.skill_points("Snowflake, BigQuery, Airflow, dbt and Kafka")
    assert pts == 5 and len(matched) == 5


def test_aws_only_stack_is_penalised(scorer):
    aws = scorer.score(job("Data Engineer", description="AWS Glue, Redshift, EMR, PySpark, SQL"), NOW)
    azure = scorer.score(job("Data Engineer", description="Azure Databricks, Redshift migration, PySpark, SQL"), NOW)
    assert aws.score_breakdown["penalty"] == -12
    assert azure.score_breakdown["penalty"] == 0


@pytest.mark.parametrize("title,desc,location,walk_in,ok", [
    ("Walk-in || Azure Data Engineer", "", "Pune, Bengaluru, India", True, False),
    ("Azure Data Engineer", "Walk in interview on Saturday 10am", "Chennai", True, False),
    ("Data Engineer - Walkin Drive", "", "Hyderabad, Telangana", True, True),
    ("Data Engineer", "Weekend drive for Azure roles", "Hyderabad / Pune", True, True),
    ("Data Engineer", "Hybrid role, 3 days in office", "Pune", False, True),
    ("Walk-in Drive - Data Engineer (Virtual)", "", "GJ, IN", True, True),  # online: attend from anywhere
])
def test_walk_ins_only_in_hyderabad(scorer, title, desc, location, walk_in, ok):
    assert scorer.walk_in_status(job(title, location, desc)) == (walk_in, ok)
