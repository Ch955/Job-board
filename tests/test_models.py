from jobradar.models import Job, clean_text, to_iso


def test_clean_text_strips_double_escaped_html():
    assert clean_text("&lt;p&gt;Hello &amp;amp; &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;") == "Hello & world"


def test_to_iso_accepts_many_formats():
    assert to_iso("2026-10-01") == "2026-10-01T00:00:00+00:00"
    assert to_iso("2026-10-01T10:00:00Z") == "2026-10-01T10:00:00+00:00"
    assert to_iso(1790000000) == to_iso(1790000000000)
    assert to_iso("not a date") is None
    assert to_iso(None) is None


def test_dedupe_key_normalizes_company_and_city():
    a = Job(title="Senior Data Engineer", company="Databricks", location="Bengaluru, India", url="a", source="greenhouse")
    b = Job(title="Senior  Data Engineer!", company="Databricks Inc.", location="Bangalore, Karnataka", url="b", source="adzuna")
    assert a.id == b.id


def test_different_city_is_a_different_job():
    a = Job(title="Data Engineer", company="X", location="Pune", url="a", source="s")
    b = Job(title="Data Engineer", company="X", location="Hyderabad", url="b", source="s")
    assert a.id != b.id


def test_to_dict_drops_full_description():
    j = Job(title="Data Engineer", company="X", location="Pune", url="u", source="s", description="long text")
    assert "description" not in j.to_dict()
    assert Job.from_dict(j.to_dict()).id == j.id
