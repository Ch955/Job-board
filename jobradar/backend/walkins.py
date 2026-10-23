"""Walk-in interviews: events you attend in person, found in real job postings.

A posting counts as a walk-in only when its own text says so (the walk_in patterns in
config.yaml). Dates, times, venue, documents, registration and pay are read from that
text; nothing is guessed. An event is "confirmed" only when the posting gives both a
date and a venue/address; otherwise it's shown as "unconfirmed". Past dates are expired.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime, timedelta, timezone

from ..models import Job, norm_company
from ..scoring import Scorer
from .matching import UserMatcher, _role_patterns, city_label

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
D = r"(\d{1,2})(?:st|nd|rd|th)?"
DAY_MONTH = re.compile(rf"{D}(?:\s*(?:-|–|to|&|and|,)\s*{D})?\s+(?:of\s+)?{MON}(?:,?\s*(\d{{4}}))?", re.I)
MONTH_DAY = re.compile(rf"{MON}\s+{D}(?:\s*(?:-|–|to|&)\s*{D})?,?\s*(\d{{4}})?", re.I)
NUMERIC = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d\d)\b")
TIME = re.compile(r"(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))\s*(?:-|–|to|till|until)\s*(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))"
                  r"|(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm))\s*onwards", re.I)
VENUE = re.compile(r"(?i:venue|address|interview location|location of (?:the )?interview|walk[- ]?in (?:venue|address|location))"
                   r"\s*(?:details)?\s*[:\-–]\s*(.{8,220}?)"
                   r"(?=\.\s+[A-Z][A-Za-z ]{1,25}:|\.\s*$|\s+(?i:date|time|timings?|contact|documents?|note|registration|salary|ctc|experience)\b\s*:|$|\n)",
                   re.S)
DOCS = re.compile(r"(?:documents?(?: required| to (?:carry|bring))?\s*[:\-–]\s*|(?:please |kindly )?(?:carry|bring)\s+(?:your\s+)?)([^.\n]{5,200})", re.I)
REGISTER = re.compile(r"([^.\n]{0,120}\b(?:register|registration|pre-?register)\b[^\n]{0,200}?)(?=\.\s|\.$|\n|$)", re.I)
SALARY = re.compile(r"(?:₹|\brs\.?|\binr)\s*\d[\d.,]*\s*(?:lpa|lakhs?|l|k|per annum|pa)?(?:\s*(?:-|–|to)\s*(?:₹|rs\.?|inr)?\s*\d[\d.,]*\s*(?:lpa|lakhs?|l|k)?)?"
                    r"|\b\d+(?:\.\d+)?\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*(?:lpa|lakhs?)\b|\b\d+(?:\.\d+)?\s*lpa\b", re.I)

# Approximate city centres for the travel-radius filter (km between cities, not street level)
COORDS = {"Hyderabad": (17.385, 78.487), "Bengaluru": (12.972, 77.594), "Chennai": (13.083, 80.270), "Pune": (18.520, 73.856),
          "Mumbai": (19.076, 72.878), "Delhi NCR": (28.614, 77.209), "Kolkata": (22.573, 88.364), "Ahmedabad": (23.023, 72.571),
          "Kochi": (9.931, 76.267), "Jaipur": (26.912, 75.787), "Chandigarh": (30.733, 76.779), "Coimbatore": (11.017, 76.956),
          "Indore": (22.720, 75.858), "Bhubaneswar": (20.296, 85.825), "Visakhapatnam": (17.687, 83.218)}


def is_walkin(job: Job, config: dict) -> bool:
    pats = [re.compile(p, re.I) for p in (config.get("walk_in") or {}).get("patterns", [])]
    text = f"{job.title} {job.description or job.description_snippet}"
    return any(p.search(text) for p in pats)


def _year_for(month: int, day: int, year: str | None, today: date) -> date | None:
    try:
        if year:
            return date(int(year), month, day)
        d = date(today.year, month, day)
        return d if d >= today - timedelta(days=60) else date(today.year + 1, month, day)
    except ValueError:
        return None


def dates_in(text: str, today: date) -> list[date]:
    found: set[date] = set()
    for m in DAY_MONTH.finditer(text):
        mon = MONTHS[m.group(3)[:3].lower()]
        for dd in (m.group(1), m.group(2)):
            if dd:
                d = _year_for(mon, int(dd), m.group(4), today)
                if d:
                    found.add(d)
        if m.group(1) and m.group(2) and re.search(r"-|–|to", m.group(0)):  # a range: every day in it
            a, b = _year_for(mon, int(m.group(1)), m.group(4), today), _year_for(mon, int(m.group(2)), m.group(4), today)
            if a and b and 0 < (b - a).days <= 7:
                found.update(a + timedelta(days=i) for i in range((b - a).days + 1))
    for m in MONTH_DAY.finditer(text):
        mon = MONTHS[m.group(1)[:3].lower()]
        for dd in (m.group(2), m.group(3)):
            if dd:
                d = _year_for(mon, int(dd), m.group(4), today)
                if d:
                    found.add(d)
    for m in NUMERIC.finditer(text):
        try:
            found.add(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))  # Indian order: dd/mm/yyyy
        except ValueError:
            pass
    return sorted(d for d in found if today - timedelta(days=400) <= d <= today + timedelta(days=180))


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" .,;:-–")[:220]


def extract(job: Job, today: date) -> dict:
    text = job.description or job.description_snippet or ""
    full = f"{job.title}\n{text}"
    when = dates_in(full, today)
    t = TIME.search(full)
    time_txt = (f"{t.group(1)} – {t.group(2)}" if t.group(1) else f"{t.group(3)} onwards") if t else None
    v = VENUE.search(text)
    venue = _clean(v.group(1)) if v else None
    city_src = venue or job.location
    city = city_label(city_src) if city_src else "India"
    docs = DOCS.search(text)
    reg = REGISTER.search(text)
    sal = SALARY.search(text)
    lo, hi = Scorer.required_range(text)
    upcoming = [d for d in when if d >= today]
    status = ("expired" if when and not upcoming else
              "confirmed" if upcoming and venue else "unconfirmed")
    missing = [x for x, ok in (("date", bool(when)), ("venue", bool(venue))) if not ok]
    key = "|".join((norm_company(job.company), re.sub(r"\W+", " ", job.title.lower()).strip(), city,
                    (upcoming or when or [None])[0].isoformat() if (upcoming or when) else "-"))
    return {
        "id": "w-" + hashlib.sha1(key.encode()).hexdigest()[:12], "job_id": job.id,
        "title": job.title, "company": job.company, "city": city, "venue": venue, "location": job.location,
        "dates": [d.isoformat() for d in (upcoming or when)], "time": time_txt,
        "experience": {"min": lo, "max": hi}, "salary": _clean(sal.group(0)) if sal else None,
        "documents": _clean(docs.group(1)) if docs else None, "registration": _clean(reg.group(1)) if reg else None,
        "status": status, "missing": missing, "url": job.url, "source": job.source,
        "retrieved": job.first_seen, "posted": job.posted_date,
    }


def km(a: str, b: str) -> float | None:
    if a == b:
        return 0.0
    if a not in COORDS or b not in COORDS:
        return None
    (la1, lo1), (la2, lo2) = COORDS[a], COORDS[b]
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    return 6371 * math.acos(min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)))


def for_user(pool: list[Job], base_config: dict, profile: dict, now: datetime | None = None) -> dict:
    """This user's walk-ins: their walk-in cities (and radius), dates, roles, industries, experience, score."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    cities = [city_label(c) if city_label(c) != "India" else c for c in profile.get("walkin_cities") or []]
    out = {"upcoming": [], "unconfirmed": [], "expired": 0, "cities": cities, "checked": now.isoformat(timespec="seconds")}
    if not cities:
        return out
    # Score on role, skills and experience; where it is was already checked above (venue, radius)
    matcher = UserMatcher(base_config, {**profile, "threshold": 0, "search_scope": "india", "locations": [],
                                        "walkin_cities": [], "work_modes": ["onsite", "hybrid", "remote"]})
    roles = profile.get("walkin_roles") or profile.get("target_roles") or [profile.get("current_role", "")]
    role_rx = [rx for rx, pts, _ in _role_patterns([r for r in roles if r]) if pts >= 18]
    radius = profile.get("walkin_radius_km")
    seen: set[str] = set()
    for job in pool:
        if not is_walkin(job, base_config):
            continue
        ev = extract(job, today)
        if ev["id"] in seen:
            continue  # the same company, role, city and date announced twice
        dist = min((d for d in (km(ev["city"], c) for c in cities) if d is not None), default=None)
        in_city = ev["city"] in cities or any(c.lower() in (ev["venue"] or ev["location"] or "").lower() for c in cities)
        if not in_city and not (radius and dist is not None and dist <= radius):
            continue
        if role_rx and not any(rx.search(job.title) for rx in role_rx):
            continue
        inds = profile.get("walkin_industries") or []
        if inds and not any(i.lower() in f"{job.title} {job.description}".lower() for i in inds):
            continue
        lo, hi = ev["experience"]["min"], ev["experience"]["max"]
        wmin, wmax = profile.get("walkin_min_years"), profile.get("walkin_max_years")
        if lo is not None and wmax is not None and lo > wmax:
            continue
        if hi is not None and wmin is not None and hi < wmin:
            continue
        if ev["dates"] and (profile.get("walkin_from") or profile.get("walkin_to")):
            f, t = profile.get("walkin_from") or "0000", profile.get("walkin_to") or "9999"
            if not any(f <= d <= t for d in ev["dates"]):
                continue
        e = matcher.explain(job, now)
        if e["score"] < int(profile.get("walkin_min_score", 50)):
            continue
        seen.add(ev["id"])
        ev.update(score=e["score"], matched_skills=e["matched_skills"], distance_km=None if dist is None else round(dist))
        if ev["status"] == "expired":
            out["expired"] += 1
        else:
            out["upcoming" if ev["status"] == "confirmed" else "unconfirmed"].append(ev)
    out["upcoming"].sort(key=lambda e: (e["dates"][0], -e["score"]))
    out["unconfirmed"].sort(key=lambda e: -e["score"])
    return out
