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


@pytest.mark.parametrize("raw,city", [
    ("Bangalore Urban, Karnataka, India", "Bengaluru"), ("KA, IN", "Bengaluru"), ("Bengaluru East, Karnataka, India", "Bengaluru"),
    ("Greater Hyderabad Area", "Hyderabad"), ("TS, IN", "Hyderabad"), ("Gurugram, Haryana, India", "Delhi NCR"),
    ("Pune Division, Maharashtra, India", "Pune"), ("MH, IN", "Mumbai"), ("Chennai, TN, India", "Chennai"),
    ("PB, IN", "Chandigarh"), ("India", "India"),
])
def test_one_city_name_per_place(raw, city):
    assert matching.city_label(raw) == city


def test_matches_carry_the_city_label():
    m = matching.UserMatcher(load_config(), PROFILE)
    got = m.match([job("Data Engineer", "Airflow dbt Snowflake PySpark Python SQL, 2-4 years experience",
                       location="Pune Division, Maharashtra, India")], NOW)
    assert got[0]["score_breakdown"]["location_label"] == "Pune"


def test_city_spelling_doesnt_matter():
    p = {**PROFILE, "locations": ["Bangalore"], "anywhere_in_india": False}
    e = matching.UserMatcher(load_config(), p).explain(
        job("Data Engineer", "Airflow dbt, 2-4 years experience", location="Bengaluru East, Karnataka, India"), NOW)
    assert e["breakdown"]["location"] == 20


# ---------------------------------------------------------------- job preferences
def prefs(**kw):
    return profile.clean({**{k: v for k, v in PROFILE.items() if k not in ("work_modes", "search_scope", "relocation",
                                                                              "remote_ok", "anywhere_in_india")}, **kw})


def titles(p, jobs):
    return sorted(g["location"] for g in matching.UserMatcher(load_config(), {**p, "threshold": 0}).match(jobs, NOW))


JOBS3 = [job("Data Engineer", "Airflow dbt, 2-4 years", location="Hyderabad, Telangana, India"),
         job("Data Engineer", "Airflow dbt, 2-4 years", location="Chennai, Tamil Nadu, India"),
         job("Data Engineer", "Airflow dbt, 2-4 years. Hybrid, 3 days office", location="Pune, Maharashtra, India"),
         Job(title="Data Engineer", company="Acme", location="Remote", url="https://x", source="lever", remote=True,
             posted_date="2026-10-22", description="Airflow dbt, 2-4 years. HQ in Gurugram")]


def test_multiple_cities_selected_only_and_no_relocation():
    p = prefs(locations=["Hyderabad", "Chennai"], search_scope="selected", relocation="no")
    assert titles(p, JOBS3) == ["Chennai, Tamil Nadu, India", "Hyderabad, Telangana, India", "Remote"]


def test_remote_job_isnt_excluded_for_its_company_city_but_respects_work_mode():
    p = prefs(locations=["Hyderabad"], search_scope="selected", relocation="no", work_modes=["onsite", "hybrid"])
    assert "Remote" not in titles(p, JOBS3)
    p = prefs(locations=["Hyderabad"], search_scope="selected", relocation="no", work_modes=["remote"])
    assert titles(p, JOBS3) == ["Remote"]   # stated hybrid Pune job and unstated on-site ones go


def test_anywhere_in_india_and_relocation_keep_other_cities():
    assert len(titles(prefs(locations=["Hyderabad"], search_scope="india"), JOBS3)) == 4
    assert len(titles(prefs(locations=["Hyderabad"], search_scope="selected", relocation="yes"), JOBS3)) == 4


def test_old_profiles_keep_working():
    p = profile.clean({"full_name": "A", "email": "a@example.com", "locations": ["Pune"], "anywhere_in_india": True,
                       "remote_ok": False})
    assert p["search_scope"] == "india" and p["work_modes"] == ["onsite", "hybrid"] and p["relocation"] == "flexible"
    assert p["remote_ok"] is False and p["anywhere_in_india"] is True


def test_walkin_preferences_validated_and_copyable():
    p = profile.clean({"locations": ["Hyderabad", "Chennai"], "walkin_same_as_jobs": True, "walkin_radius_km": 30,
                       "walkin_from": "2026-10-24", "walkin_to": "2026-11-15"})
    assert p["walkin_cities"] == ["Hyderabad", "Chennai"] and p["walkin_radius_km"] == 30
    for bad in ({"walkin_from": "24/10/2026"}, {"walkin_from": "2026-11-02", "walkin_to": "2026-11-01"},
                {"work_modes": ["office"]}, {"relocation": "maybe"}, {"search_scope": "world"}):
        with pytest.raises(profile.ProfileError):
            profile.clean(bad)
