"""Score the shared job pool for one user, with a plain-language explanation.

Score 0-100 = role 25 + skills 35 + location 20 + experience 10 + recency 10,
all from the user's confirmed profile (no hard-coded person). Only real fetched
postings are scored; nothing is generated. Jobs below the user's threshold
(default 70) are kept out of their list.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import skills_vocab
from ..models import Job
from ..scoring import Scorer, age_days
from .profile import DEFAULT_THRESHOLD

GENERIC = {"senior", "junior", "lead", "associate", "staff", "principal", "sr", "jr", "ii", "iii", "i", "the", "and", "of"}
DEGREE_ASK = [(re.compile(p, re.I), lvl) for p, lvl in (
    (r"\b(ph\.?d|doctorate)\b", "PhD"),
    (r"\b(master'?s|m\.?\s?tech|mca|m\.?s\.?)\b.{0,40}\b(required|must|mandatory)\b", "Master's"),
    (r"\b(bachelor'?s|b\.?\s?tech|b\.?e\.?|graduate|degree)\b", "Bachelor's"),
)]
LEVEL = {"Diploma": 1, "Bachelor's": 2, "Master's": 3, "PhD": 4}
# The listing says you must upload files: an application for the laptop, not the phone
FILES_REQUIRED = re.compile(r"upload (your |an updated )?(resume|cv)|attach (your )?(resume|cv)|cover letter (is )?(required|mandatory)"
                            r"|portfolio (is )?required", re.I)

# One name per city for the app's Location filter ("Bangalore Urban, Karnataka" and "KA, IN" -> Bengaluru)
CITIES = [
    ("Bengaluru", r"bangalore|bengaluru|\bka\b|karnataka"), ("Hyderabad", r"hyderabad|secunderabad|hitec|gachibowli|\bts\b|telangana"),
    ("Chennai", r"chennai|\btn\b|tamil nadu"), ("Pune", r"\bpune\b"), ("Mumbai", r"mumbai|thane|navi mumbai"),
    ("Delhi NCR", r"delhi|\bncr\b|gurgaon|gurugram|noida|faridabad|ghaziabad|\bhr\b|haryana"),
    ("Kolkata", r"kolkata|west bengal|\bwb\b"), ("Ahmedabad", r"ahmedabad|gandhinagar|gujarat|\bgj\b"),
    ("Kochi", r"kochi|cochin|kerala|trivandrum|thiruvananthapuram|\bkl\b"), ("Jaipur", r"jaipur|rajasthan|\brj\b"),
    ("Chandigarh", r"chandigarh|mohali|punjab|\bpb\b"), ("Coimbatore", r"coimbatore"), ("Indore", r"indore|madhya pradesh"),
    ("Bhubaneswar", r"bhubaneswar|odisha"), ("Visakhapatnam", r"visakhapatnam|vizag|andhra"),
    ("Mumbai", r"\bmh\b|maharashtra"),  # after Pune, so "Pune, Maharashtra" stays Pune
]
_CITY_RX = [(name, re.compile(rx, re.I)) for name, rx in CITIES]


def _city_rx(city: str) -> str:
    """'Bangalore' also matches 'Bengaluru', 'KA, IN'...; unknown places match as typed."""
    name = next((n for n, rx in _CITY_RX if rx.search(city)), None)
    return "|".join(rx for n, rx in CITIES if n == name) if name else re.escape(city.lower())


def city_label(location: str, remote: bool = False) -> str:
    for name, rx in _CITY_RX:
        if rx.search(location or ""):
            return name
    return "Remote (India)" if remote else "India"


def _role_patterns(roles: list[str]) -> list[tuple[re.Pattern, int, str]]:
    out = []
    for role in roles:
        words = [w for w in re.findall(r"[a-z0-9+#.]+", role.lower()) if w not in GENERIC]
        if not words:
            continue
        out.append((re.compile(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", re.I), 25, role))
        if len(words) >= 2:  # "Data Engineer" also fits "Data Platform Engineer", for fewer points
            out.append((re.compile(rf"\b{re.escape(words[0])}\b.*\b{re.escape(words[-1])}\b", re.I), 18, role))
            out.append((re.compile(rf"\b{re.escape(words[-1])}\b", re.I), 8, role))
    return out


def scorer_config(base: dict, profile: dict) -> dict:
    """The shared config.yaml with this user's role, skills, cities and experience swapped in."""
    roles = profile.get("target_roles") or ([profile["current_role"]] if profile.get("current_role") else [])
    years = profile.get("years_experience")
    lo = profile.get("min_years") if profile.get("min_years") is not None else (max(0, int(years) - 1) if years is not None else 0)
    hi = profile.get("max_years") if profile.get("max_years") is not None else (int(years) + 3 if years is not None else 50)
    loc = dict(base.get("location") or {})
    cities = {c: [_city_rx(c)] for c in profile.get("locations") or []}
    scope = profile.get("search_scope") or ("india" if profile.get("anywhere_in_india") or not cities else "selected")
    other = 20 if scope == "india" or not cities else {"yes": 14, "flexible": 10}.get(profile.get("relocation"), 6)
    remote_ok = "remote" in (profile.get("work_modes") or (["remote"] if profile.get("remote_ok", True) else []))
    loc.update(preferred=cities or loc.get("preferred", {}), points_preferred=20,
               points_india_other=other, points_remote=20 if remote_ok else 6)
    return {**base,
            "title": {"rules": [{"pattern": p.pattern, "points": pts} for p, pts, _ in _role_patterns(roles)],
                      "exclude": [r"\bintern(ship)?\b"] if (years or 0) >= 1 else [], "penalties": []},
            "skills": {"max_points": 35, "keywords": [
                {"name": s, "weight": 5, "patterns": skills_vocab.patterns_for(s)} for s in profile.get("skills") or []]},
            "location": loc,
            "experience": {"points": 10, "min_years": lo, "max_years": hi, "unknown_points": 6},
            "walk_in": {**(base.get("walk_in") or {}), "allowed_locations": [
                _city_rx(c) for c in (profile.get("walkin_cities") or profile.get("locations") or [])]}}


class UserMatcher:
    def __init__(self, base_config: dict, profile: dict):
        self.profile = profile
        self.cfg = scorer_config(base_config, profile)
        self.scorer = Scorer(self.cfg)
        self.mine = skills_vocab.covered(profile.get("skills") or [])  # PySpark also covers Spark, etc.
        self.threshold = int(profile.get("threshold", DEFAULT_THRESHOLD))
        self.level = max((LEVEL.get(d, 0) for d in profile.get("degree_levels") or []), default=None)

    def explain(self, job: Job, now: datetime | None = None) -> dict:
        text = f"{job.title} {job.description or job.description_snippet}"
        s = self.scorer
        title_pts = s.title_points(job.title)
        loc_pts, loc_label, loc_ok = s.location_points(job)
        exp_pts, ylo, yhi, exp_ok = s.experience_points(job.description or job.description_snippet)
        rec_pts, days = s.recency_points(job, now)
        walk_in, walk_ok = s.walk_in_status(job)
        asked = [k for k in skills_vocab.find_skills(text)]
        matched = [k for k in self.profile.get("skills") or []
                   if any(re.search(p, text, re.I) for p in skills_vocab.patterns_for(k))]
        missing = [k for k in asked if k.lower() not in self.mine and k not in ("Agile", "Jira", "Excel", "REST APIs")]
        # Skills: what share of the job's asks you cover, plus depth of overlap
        coverage = len([k for k in asked if k.lower() in self.mine]) / len(asked) if asked else 0.5
        skill_pts = round(35 * min(1.0, 0.65 * coverage + 0.35 * min(1.0, len(matched) / 6)))
        need = next((lvl for rx, lvl in DEGREE_ASK if rx.search(text)), None)
        qual_ok = None if need is None or self.level is None else self.level >= LEVEL[need]
        score = max(0, min(100, title_pts + skill_pts + loc_pts + exp_pts + rec_pts))
        if not loc_ok or not walk_ok:
            score = min(score, 40)
        if not exp_ok:
            score = min(score, 55)
        if title_pts == 0:
            score = min(score, 45)
        if qual_ok is False:
            score = min(score, 60)
        parts = []
        parts.append(f"Role: {'matches' if title_pts >= 18 else 'related' if title_pts else 'different from'} your target role.")
        if matched:
            parts.append(f"You have {len(matched)} of the skills it mentions ({', '.join(matched[:5])}).")
        if missing:
            parts.append(f"Not on your profile: {', '.join(missing[:5])}.")
        if ylo is not None:
            ask = f"{ylo}-{yhi}" if yhi else f"{ylo}+"
            parts.append(f"Asks for {ask} years; " + ("fits your experience." if exp_ok else "outside your experience range."))
        if need:
            parts.append(f"Asks for a {need} degree" + ("." if qual_ok is None else "; you have it." if qual_ok else "; your profile shows a lower degree."))
        return {
            "score": score,
            "breakdown": {"role": title_pts, "skills": skill_pts, "location": loc_pts, "experience": exp_pts, "recency": rec_pts,
                          "location_label": city_label(job.location, job.remote) if loc_ok else loc_label,
                          "walk_in": walk_in, "years_required": ylo, "years_max": yhi},
            "matched_skills": matched, "missing_skills": missing[:10],
            "experience": {"min": ylo, "max": yhi, "fits": exp_ok},
            "role_fit": "match" if title_pts >= 18 else "related" if title_pts else "different",
            "qualifications": {"degree_asked": need, "fits": qual_ok},
            "location": {"label": loc_label, "fits": loc_ok}, "walk_in": walk_in, "age_days": days,
            "files_required": bool(FILES_REQUIRED.search(text)),
            "summary": " ".join(parts),
        }

    def fits_preferences(self, job: Job) -> tuple[bool, str]:
        """Your job preferences: cities / anywhere in India, relocation, on-site / hybrid / remote."""
        from ..apply_type import work_mode
        p = self.profile
        modes = p.get("work_modes") or (["onsite", "hybrid", "remote"] if p.get("remote_ok", True) else ["onsite", "hybrid"])
        mode = job.work_mode or work_mode(job.remote, f"{job.title} {job.location} {job.description or job.description_snippet}")
        if mode and mode not in modes:
            return False, f"{mode} job, and you chose {', '.join(modes)}"
        if not mode and modes == ["remote"]:
            return False, "not listed as remote"
        if mode == "remote":
            return True, "Remote (India)"   # where the company sits doesn't matter for a remote job
        cities = p.get("locations") or []
        scope = p.get("search_scope") or ("india" if p.get("anywhere_in_india") or not cities else "selected")
        if not cities or scope == "india":
            return True, ""
        here = city_label(job.location, job.remote)
        if any(city_label(c) == here or c.lower() in (job.location or "").lower() for c in cities):
            return True, ""
        if here == "India":
            return True, "Location not specific in the listing"
        if p.get("relocation") in ("yes", "flexible"):
            return True, f"In {here}: you're open to relocating"
        return False, f"In {here}, outside your cities"

    def match(self, pool: list[Job], now: datetime | None = None, funnel: dict | None = None) -> list[dict]:
        """Jobs at or above the user's threshold, best first, each with its explanation.
        `funnel` (optional) is filled with how many jobs got past each step, to explain an empty list."""
        now = now or datetime.now(timezone.utc)
        out = []
        f = funnel if funnel is not None else {}
        f.update(pool=len(pool), recent=0, role=0, preferences=0, threshold=0, best_below=None)
        for job in pool:
            if (age_days(job, now) or 0) > 30:
                continue
            f["recent"] += 1
            f["role"] += self.scorer.title_points(job.title) > 0   # counted only: scoring caps other roles at 45
            ok, why = self.fits_preferences(job)
            if not ok:
                continue
            f["preferences"] += 1
            e = self.explain(job, now)
            if e["score"] < self.threshold:
                f["best_below"] = max(f["best_below"] or 0, e["score"])
            if why:
                e["summary"] += f" {why}."
            if e["score"] >= self.threshold:
                d = job.to_dict()
                d.update(score=e["score"], matched_keywords=e["matched_skills"], score_breakdown=e["breakdown"], match=e)
                out.append(d)
        out.sort(key=lambda d: (-d["score"], d.get("posted_date") or ""))
        f["threshold"] = len(out)
        return out
