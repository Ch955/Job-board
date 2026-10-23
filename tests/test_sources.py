"""Each parser against an API-shaped fixture."""

from jobradar.offline import render_fixture
from jobradar.sources import adzuna, arbeitnow, ashby, greenhouse, jooble, lever, remotive, smartrecruiters


def test_greenhouse():
    jobs = greenhouse.parse(render_fixture("greenhouse_databricks.json"), "Databricks")
    assert [j.title for j in jobs][0] == "Senior Data Engineer"
    j = jobs[0]
    assert j.company == "Databricks" and j.location == "Bengaluru, India"
    assert "Azure Data Factory" in j.description and "<" not in j.description
    assert j.posted_date and j.url.startswith("https://")


def test_lever_merges_lists_and_reads_epoch_ms():
    j = lever.parse(render_fixture("lever_cred.json"), "CRED")[0]
    assert j.title == "Data Engineer II" and j.location == "Bangalore"
    assert "2-5 years" in j.description and j.posted_date.startswith("20")


def test_ashby_remote_flag():
    j = ashby.parse(render_fixture("ashby_atlan.json"), "Atlan")[0]
    assert j.remote and j.location == "Remote - India"


def test_smartrecruiters_listing_and_detail():
    items = render_fixture("smartrecruiters_visa_list.json")["content"]
    j = smartrecruiters.parse_listing(items[0], "Visa", "Visa")
    assert j.location == "Hyderabad, TS, India"
    assert j.url == "https://jobs.smartrecruiters.com/Visa/sr-1"
    assert "ADF v2" in smartrecruiters.parse_detail(render_fixture("smartrecruiters_visa_detail.json"))


def test_adzuna():
    jobs = adzuna.parse(render_fixture("adzuna.json"))
    assert jobs[0].company == "Example Analytics Pvt Ltd" and jobs[0].location == "Pune, Maharashtra"


def test_jooble():
    jobs = jooble.parse(render_fixture("jooble.json"))
    assert jobs[0].url == "https://example.com/jooble/1" and "PySpark" in jobs[0].description


def test_remotive_marks_remote_with_region():
    jobs = remotive.parse(render_fixture("remotive.json"))
    assert all(j.remote for j in jobs)
    assert jobs[0].location == "Remote (USA Only)"


def test_arbeitnow_remote_only_and_epoch_seconds():
    jobs = arbeitnow.parse(render_fixture("arbeitnow.json"), remote_only=True)
    assert len(jobs) == 1 and jobs[0].location == "Remote (Berlin)" and jobs[0].posted_date
    assert len(arbeitnow.parse(render_fixture("arbeitnow.json"), remote_only=False)) == 2
