"""Walk-ins: only real walk-in postings, details read from the text, never invented."""

from datetime import date, datetime, timezone

from jobradar.backend import profile, walkins
from jobradar.config import load_config
from jobradar.models import Job

NOW = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
TODAY = NOW.date()
BASE = load_config()


def W(title, desc, location="Hyderabad, Telangana, India", company="Northwind", url="https://boards.greenhouse.io/nw/1"):
    return Job(title=title, company=company, location=location, url=url, source="greenhouse",
               posted_date="2026-10-23", description=desc)


FULL = ("Walk-in drive for Data Engineers. Date: 28th October 2026. Time: 10:00 AM - 2:00 PM. "
        "Venue: Northwind Tower, Hitec City, Madhapur, Hyderabad 500081. Experience: 2-5 years. "
        "Skills: SQL, Python, Airflow, dbt. CTC: 6 - 12 LPA. Documents to carry: updated resume, ID proof. "
        "Please register at https://careers.northwind.example/walkin before attending.")
P = profile.clean({"full_name": "Asha", "email": "a@example.com", "target_roles": ["Data Engineer"], "years_experience": 3,
                   "skills": ["SQL", "Python", "Airflow", "dbt"], "walkin_cities": ["Hyderabad"], "walkin_min_score": 30})


def test_all_published_details_are_read():
    ev = walkins.extract(W("Walk-in: Data Engineer", FULL), TODAY)
    assert ev["status"] == "confirmed" and ev["dates"] == ["2026-10-28"] and ev["city"] == "Hyderabad"
    assert ev["time"] == "10:00 AM – 2:00 PM" and "Hitec City" in ev["venue"]
    assert ev["experience"] == {"min": 2, "max": 5} and "12 LPA" in ev["salary"]
    assert "resume" in ev["documents"] and "register" in ev["registration"].lower()
    assert ev["url"] == "https://boards.greenhouse.io/nw/1"


def test_missing_date_or_venue_is_unconfirmed_not_invented():
    ev = walkins.extract(W("Walk-in: Data Engineer", "Walk-in interviews this week. Experience 2-5 years. SQL, Python."), TODAY)
    assert ev["status"] == "unconfirmed" and ev["dates"] == [] and ev["venue"] is None and ev["time"] is None
    assert set(ev["missing"]) == {"date", "venue"} and ev["salary"] is None


def test_past_dates_are_expired():
    ev = walkins.extract(W("Walk-in: Data Engineer", FULL.replace("28th October 2026", "2nd October 2026")), TODAY)
    assert ev["status"] == "expired"


def test_date_formats_and_ranges():
    assert walkins.dates_in("Date: 27/10/2026", TODAY) == [date(2026, 10, 27)]
    assert walkins.dates_in("on Oct 30, 2026", TODAY) == [date(2026, 10, 30)]
    assert walkins.dates_in("26th - 28th Oct", TODAY) == [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]


def test_only_real_walkins_and_only_your_cities():
    pool = [W("Walk-in: Data Engineer", FULL),
            W("Data Engineer", "Regular role. SQL, Python, Airflow, dbt. 2-5 years."),             # not a walk-in
            W("Walk-in: Data Engineer", FULL.replace("Northwind Tower, Hitec City, Madhapur, Hyderabad 500081", "Contoso Park, Guindy, Chennai"),
              location="Chennai, India", company="Contoso")]
    r = walkins.for_user(pool, BASE, P, NOW)
    assert [e["company"] for e in r["upcoming"]] == ["Northwind"] and r["unconfirmed"] == []


def test_radius_reaches_a_nearby_city_only_when_set():
    pool = [W("Walk-in: Data Engineer", FULL.replace("Northwind Tower, Hitec City, Madhapur, Hyderabad 500081", "MG Road, Bengaluru"),
              location="Bengaluru, India")]
    assert walkins.for_user(pool, BASE, P, NOW)["upcoming"] == []
    far = walkins.for_user(pool, BASE, {**P, "walkin_radius_km": 600}, NOW)["upcoming"]   # Hyd-Blr is ~500 km
    assert len(far) == 1 and 450 < far[0]["distance_km"] < 560


def test_duplicates_merge_and_expired_are_counted_not_listed():
    pool = [W("Walk-in: Data Engineer", FULL), W("Walk-in: Data Engineer", FULL, url="https://x/dup"),
            W("Walk-in: Data Engineer", FULL.replace("28th October 2026", "2nd October 2026"), company="Old Co")]
    r = walkins.for_user(pool, BASE, P, NOW)
    assert len(r["upcoming"]) == 1 and r["expired"] == 1


def test_no_cities_means_empty_with_reason():
    r = walkins.for_user([W("Walk-in: Data Engineer", FULL)], BASE, {**P, "walkin_cities": []}, NOW)
    assert r["upcoming"] == [] and r["cities"] == []


def test_date_window_roles_and_experience_filters():
    pool = [W("Walk-in: Data Engineer", FULL)]
    assert walkins.for_user(pool, BASE, {**P, "walkin_from": "2026-11-01"}, NOW)["upcoming"] == []
    assert walkins.for_user(pool, BASE, {**P, "walkin_roles": ["Java Developer"]}, NOW)["upcoming"] == []
    assert walkins.for_user(pool, BASE, {**P, "walkin_max_years": 1}, NOW)["upcoming"] == []
    assert walkins.for_user(pool, BASE, {**P, "walkin_min_score": 100}, NOW)["upcoming"] == []
