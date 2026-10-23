"""Application type comes from evidence (source + real link), never from the site's name alone."""

import pytest

from jobradar import apply_type as at
from jobradar.models import Job


@pytest.mark.parametrize("source,url,direct,expected", [
    ("greenhouse", "https://boards.greenhouse.io/acme/jobs/1", "", "company_site"),
    ("greenhouse", "https://www.databricks.com/company/careers/x?gh_jid=1", "", "company_site"),  # custom domain on ATS
    ("lever", "https://jobs.lever.co/acme/1", "", "company_site"),
    ("adzuna", "https://www.adzuna.in/land/ad/123", "", "external"),
    ("jooble", "https://jooble.org/desc/123", "", "external"),
    ("linkedin", "https://www.linkedin.com/jobs/view/1", "", "unknown"),          # never assumed Easy Apply
    ("naukri", "https://www.naukri.com/job-listings-1", "", "unknown"),
    ("linkedin", "https://www.linkedin.com/jobs/view/1", "https://acme.wd3.myworkdayjobs.com/x", "company_site"),
    ("indeed", "https://in.indeed.com/viewjob?jk=1", "https://careers.acme.com/apply/1", "company_site"),
    ("email", "https://mail.google.com/mail/u/0/#all/t1", "", "unknown"),
    ("naukri", "", "", "unknown"),
])
def test_classify(source, url, direct, expected):
    assert at.classify(source, url, direct)["apply_type"] == expected


def test_easy_apply_only_with_explicit_source_data():
    assert at.classify("linkedin", "https://www.linkedin.com/jobs/view/1")["apply_type"] != "easy_apply"
    assert at.classify("linkedin", "https://www.linkedin.com/jobs/view/1", easy_apply=True)["apply_type"] == "easy_apply"


def test_evidence_is_recorded_and_annotate_is_idempotent():
    j = Job(title="Data Engineer", company="Acme", location="Pune", url="https://www.linkedin.com/jobs/view/1",
            source="linkedin", description="Hybrid role, 3 days in office")
    at.annotate(j)
    assert j.apply_type == "unknown" and "LinkedIn" in j.apply_evidence and j.work_mode == "hybrid"
    at.annotate(j)                                      # apply_url now equals url: still not a "direct" link
    assert j.apply_type == "unknown"


@pytest.mark.parametrize("remote,text,mode", [
    (True, "", "remote"), (False, "Hybrid - 3 days office", "hybrid"), (False, "Work from office, Pune", "onsite"),
    (False, "Data engineer in Pune", ""),
])
def test_work_mode_only_when_stated(remote, text, mode):
    assert at.work_mode(remote, text) == mode


def test_old_records_without_fields_still_load():
    j = Job.from_dict({"title": "X", "company": "Y", "location": "", "url": "u", "source": "lever"})
    assert j.apply_type == "" and j.work_mode == ""
