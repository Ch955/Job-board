"""Resume upload (validation, extraction, encrypted per-user storage) and per-user matching."""

from datetime import datetime, timezone

import pytest

from jobradar.backend import matching, profile, resume_intake as ri, secretbox
from jobradar.backend.store import MemoryStore
from jobradar.config import load_config
from jobradar.models import Job
from tests.resume_fixtures import LINES, docx_bytes, pdf_bytes

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", secretbox.new_key())


@pytest.mark.parametrize("name,data,msg", [
    ("cv.pdf", b"", "empty"),
    ("cv.pdf", b"%PDF" + b"0" * (ri.MAX_BYTES + 1), "over"),
    ("cv.doc", b"\xd0\xcf\x11\xe0", ".doc"),
    ("cv.pdf", b"PK\x03\x04 not a pdf", "PDF or .docx"),
    ("cv.docx", b"%PDF-1.4", "PDF or .docx"),
    ("cv.exe", b"MZ", "PDF or .docx"),
])
def test_rejects_bad_files(name, data, msg):
    with pytest.raises(ri.ResumeError, match=msg):
        ri.kind_of(name, data)


def test_rejects_scanned_or_empty_text():
    with pytest.raises(ri.ResumeError, match="readable text"):
        ri.text_of("pdf", pdf_bytes(["Scan"]))


@pytest.mark.parametrize("kind,data", [("pdf", pdf_bytes()), ("docx", docx_bytes())])
def test_extracts_suggestions_for_review(kind, data):
    s = ri.extract(ri.text_of(kind, data), NOW)
    assert s["full_name"] == "Asha Rao" and s["email"] == "asha.rao@example.com"
    assert s["phone"] == "+91 98765 43210" and s["linkedin"].endswith("asha-rao-demo")
    assert s["years_experience"] == 3 and s["current_role"] == "Data Engineer"
    assert {"PySpark", "Airflow", "dbt", "Snowflake", "Kafka"} <= set(s["skills"])
    assert s["degree_levels"] == ["Bachelor's"] and "DP-203" in s["certifications"][0]
    assert s["projects"] and len(s["highlights"]) == 3


def test_years_from_dates_when_not_stated():
    lines = [l for l in LINES if "years of experience" not in l]
    s = ri.extract(ri.text_of("pdf", pdf_bytes(lines)), NOW)
    assert s["years_source"] == "dates" and 5 <= s["years_experience"] <= 6  # Jan 2021 -> Oct 2026, merged


def test_storage_is_encrypted_per_user_and_deletable():
    store = MemoryStore()
    data = pdf_bytes()
    out = ri.save(store, "alice", "../../My CV<>.pdf", data, NOW)
    assert out["file"]["name"] == "My CV.pdf" and out["file"]["mime"] == "application/pdf"
    assert "Asha" not in repr(store.docs) and "%PDF" not in repr(store.docs)  # nothing readable stored
    assert ri.load(store, "alice") == ("My CV.pdf", data)
    assert ri.load(store, "bob") is None
    # copying alice's chunks to bob's id doesn't make them readable as bob
    store.set("resumes", "bob", store.get("resumes", "alice"))
    store.set("resume_chunks", "bob_0", store.get("resume_chunks", "alice_0"))
    with pytest.raises(secretbox.SecretBoxError):
        ri.load(store, "bob")
    ri.delete(store, "alice")
    assert ri.load(store, "alice") is None and store.get("resume_chunks", "alice_0") is None


def test_profile_validation():
    p = profile.clean({"full_name": " Asha  Rao ", "email": "asha@example.com", "skills": ["pyspark", "PySpark", "Rust"],
                       "years_experience": "3", "threshold": 82})
    assert p["full_name"] == "Asha Rao" and p["skills"] == ["PySpark", "Rust"] and p["threshold"] == 82
    assert "phone" not in p and p["drafts_enabled"] is True                 # phone optional
    assert profile.clean({})["threshold"] == 70
    for bad in ({"threshold": 101}, {"threshold": -1}, {"email": "nope"}, {"phone": "12"}, {"skills": "x"},
                {"min_years": 5, "max_years": 2}, {"drafts_enabled": "yes"}):
        with pytest.raises(profile.ProfileError):
            profile.clean(bad)
    assert profile.is_complete({"skills": ["SQL"], "current_role": "Analyst"}) == []


PROFILE = profile.clean({"full_name": "Asha Rao", "email": "asha@example.com", "current_role": "Data Engineer",
                         "target_roles": ["Data Engineer", "Analytics Engineer"], "years_experience": 3,
                         "skills": ["Python", "SQL", "PySpark", "Airflow", "dbt", "Snowflake", "Kafka", "Databricks"],
                         "locations": ["Pune"], "degree_levels": ["Bachelor's"]})


def job(title, desc, location="Pune, India", posted="2026-10-22"):
    return Job(title=title, company="Acme", location=location, url="https://example.com/j", source="greenhouse",
               posted_date=posted, description=desc)


def test_explanation_lists_matching_and_missing_skills():
    m = matching.UserMatcher(load_config(), PROFILE)
    e = m.explain(job("Data Engineer", "3-5 years of experience with Airflow, dbt, Snowflake and Terraform. "
                                       "Bachelor's degree in CS."), NOW)
    assert e["score"] >= 70 and e["role_fit"] == "match"
    assert set(e["matched_skills"]) >= {"Airflow", "dbt", "Snowflake"} and "Terraform" in e["missing_skills"]
    assert e["experience"] == {"min": 3, "max": 5, "fits": True} and e["qualifications"]["fits"] is True
    assert "Terraform" in e["summary"] and "fits your experience" in e["summary"]


def test_threshold_and_gates():
    pool = [job("Data Engineer", "Airflow dbt Snowflake PySpark Python SQL, 2-4 years experience"),
            job("Data Engineer", "Airflow dbt Snowflake, 8+ years experience"),               # too senior
            job("Data Engineer", "Airflow dbt Snowflake PySpark", location="London, UK"),     # abroad
            job("Sales Executive", "Airflow dbt Snowflake"),                                  # wrong role
            job("Data Engineer", "Airflow dbt", posted="2026-08-01")]                         # too old
    m = matching.UserMatcher(load_config(), PROFILE)
    got = m.match(pool, NOW)
    assert [g["title"] for g in got] == ["Data Engineer"] and got[0]["match"]["experience"]["fits"]
    for t in (85, 95, 100):
        strict = matching.UserMatcher(load_config(), {**PROFILE, "threshold": t})
        assert all(g["score"] >= t for g in strict.match(pool, NOW))
    loose = matching.UserMatcher(load_config(), {**PROFILE, "threshold": 0})
    assert len(loose.match(pool, NOW)) == 4                                          # still never older than 30 days


def test_no_one_elses_skills_leak_in():
    other = profile.clean({"full_name": "Ben", "email": "b@example.com", "current_role": "Frontend Developer",
                           "skills": ["React", "TypeScript"], "years_experience": 2})
    e = matching.UserMatcher(load_config(), other).explain(job("Data Engineer", "ADF Databricks PySpark"), NOW)
    assert e["matched_skills"] == [] and e["score"] < 50
