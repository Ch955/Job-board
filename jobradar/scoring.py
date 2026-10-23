"""Rule-based 0-100 scoring driven entirely by config.yaml. No LLMs, no network.

Breakdown (default weights): title 25 + skills 35 + location 20 + experience 10
+ recency 10, minus title penalties, clamped to 0..100.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Job

_YEARS_RANGE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
_YEARS_MIN = re.compile(r"(?:minimum\s+(?:of\s+)?|at\s+least\s+)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
_EXPERIENCE_NEAR = re.compile(r"experience|exp\b", re.I)


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


def _any(patterns: list[re.Pattern], text: str) -> bool:
    return any(p.search(text) for p in patterns)


class Scorer:
    def __init__(self, config: dict):
        t = config["title"]
        self.title_rules = [(_rx(r["pattern"]), int(r["points"])) for r in t["rules"]]
        self.title_exclude = [_rx(p) for p in t.get("exclude", [])]
        self.title_penalties = [(_rx(r["pattern"]), int(r["points"])) for r in t.get("penalties", [])]

        s = config["skills"]
        self.skill_max = int(s.get("max_points", 35))
        self.skills = [
            (k["name"], int(k["weight"]), [_rx(p) for p in k["patterns"]]) for k in s["keywords"]
        ]
        mm = s.get("stack_mismatch") or {}
        self.mismatch_points = int(mm.get("points", 0))
        self.mismatch = [_rx(p) for p in mm.get("patterns", [])]
        self.mismatch_unless = [_rx(p) for p in mm.get("unless", [])]

        loc = config["location"]
        self.loc_cfg = loc
        self.preferred = {city: [_rx(p) for p in pats] for city, pats in loc["preferred"].items()}
        self.india = [_rx(p) for p in loc.get("india_terms", [])]
        self.not_india = [_rx(p) for p in loc.get("not_india_terms", [])]
        self.remote_terms = [_rx(p) for p in loc.get("remote_terms", [])]

        wi = config.get("walk_in") or {}
        self.walk_in = [_rx(p) for p in wi.get("patterns", [])]
        self.walk_in_allowed = [_rx(p) for p in wi.get("allowed_locations", [])]
        self.walk_in_virtual = [_rx(p) for p in wi.get("virtual_terms", [])]

        self.exp = config["experience"]
        self.recency = config["recency"]

    # ------------------------------------------------------------------ gates
    def is_relevant(self, job: Job) -> bool:
        """Title gate: matches a target role and isn't excluded."""
        return self.title_points(job.title) > 0 and not _any(self.title_exclude, job.title)

    # ----------------------------------------------------------------- pieces
    def title_points(self, title: str) -> int:
        return max((pts for rx, pts in self.title_rules if rx.search(title)), default=0)

    def title_penalty(self, title: str) -> int:
        return sum(pts for rx, pts in self.title_penalties if rx.search(title))

    def skill_points(self, text: str) -> tuple[int, list[str]]:
        matched, total = [], 0
        for name, weight, patterns in self.skills:
            if _any(patterns, text):
                matched.append(name)
                total += weight
        return min(total, self.skill_max), matched

    def walk_in_status(self, job: Job) -> tuple[bool, bool]:
        """Return (is_walk_in, eligible). Walk-ins only count where you can attend in person."""
        text = f"{job.title} {job.description}"
        if not self.walk_in or not _any(self.walk_in, text):
            return False, True
        if _any(self.walk_in_virtual, text):
            return True, True
        return True, (not self.walk_in_allowed) or _any(self.walk_in_allowed, job.location)

    def stack_penalty(self, text: str) -> int:
        """Negative points when the job is on another cloud's stack with no Azure in sight."""
        if self.mismatch and _any(self.mismatch, text) and not _any(self.mismatch_unless, text):
            return self.mismatch_points
        return 0

    def location_points(self, job: Job) -> tuple[int, str, bool]:
        """Return (points, label, eligible). Only India, or remote open to India, is eligible."""
        loc = job.location
        if _any(self.not_india, loc):
            return 0, "Abroad", False
        remote = job.remote or _any(self.remote_terms, loc)
        job.remote = remote
        for city, patterns in self.preferred.items():
            if _any(patterns, loc):
                return int(self.loc_cfg["points_preferred"]), city, True
        if _any(self.india, loc):
            if remote:
                return int(self.loc_cfg["points_remote"]), "Remote (India)", True
            return int(self.loc_cfg["points_india_other"]), "India", True
        # Remote with no mention of India (worldwide, US, EMEA, bare "Remote") or abroad
        return 0, "Abroad" if not remote else "Remote (not India)", False

    @staticmethod
    def required_range(text: str) -> tuple[int | None, int | None]:
        """The experience a job asks for, as (min, max-or-None).

        "2-5 years" -> (2, 5), "5+ years" -> (5, None). With several mentions the
        largest minimum wins, so "5+ years overall, 2+ with Databricks" -> 5.
        Numbers above 15 are ignored: they're almost always company boilerplate
        ("20 years of experience serving clients"), not a requirement.
        """
        best: tuple[int, int | None] | None = None
        spans = []
        for m in _YEARS_RANGE.finditer(text):
            window = text[max(0, m.start() - 60) : m.end() + 60]
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            spans.append(m.span())
            if lo <= 15 and _EXPERIENCE_NEAR.search(window) and (best is None or lo > best[0]):
                best = (lo, hi)
        for m in _YEARS_MIN.finditer(text):
            if any(a <= m.start(1) < b for a, b in spans):
                continue  # the "5 years" inside "2-5 years"
            window = text[max(0, m.start() - 60) : m.end() + 60]
            n = int(m.group(1))
            if n <= 15 and _EXPERIENCE_NEAR.search(window) and (best is None or n > best[0]):
                best = (n, None)
        return best if best else (None, None)

    @classmethod
    def required_years(cls, text: str) -> int | None:
        return cls.required_range(text)[0]

    def experience_points(self, text: str) -> tuple[int, int | None, int | None, bool]:
        """Return (points, min_years, max_years, eligible) against your min_years..max_years band."""
        lo, hi = self.required_range(text)
        full = int(self.exp["points"])
        if lo is None:
            return int(self.exp.get("unknown_points", full // 2)), None, None, True
        eligible = self.experience_eligible(lo, hi)
        return (full if eligible else 0), lo, hi, eligible

    def experience_eligible(self, lo: int | None, hi: int | None) -> bool:
        """A job fits if its range overlaps yours: it asks for at most max_years,
        and doesn't top out below min_years (e.g. "0-1 years")."""
        if lo is None:
            return True
        if lo > int(self.exp["max_years"]):
            return False
        return hi is None or hi >= int(self.exp["min_years"])

    def recency_points(self, job: Job, now: datetime | None = None) -> tuple[int, int | None]:
        days = age_days(job, now)
        if days is None:
            return 0, None
        for rule in self.recency:
            if days <= rule["max_days"]:
                return int(rule["points"]), days
        return 0, days

    # ------------------------------------------------------------------ total
    def score(self, job: Job, now: datetime | None = None) -> Job:
        text = f"{job.title} {job.description}"
        title = self.title_points(job.title)
        penalty = self.title_penalty(job.title) + self.stack_penalty(text)
        skills, matched = self.skill_points(text)
        loc, loc_label, eligible = self.location_points(job)
        exp, years, years_max, exp_ok = self.experience_points(job.description)
        rec, days = self.recency_points(job, now)
        walk_in, walk_in_ok = self.walk_in_status(job)

        job.score = max(0, min(100, title + skills + loc + exp + rec + penalty))
        job.matched_keywords = matched
        job.score_breakdown = {
            "title": title,
            "skills": skills,
            "location": loc,
            "experience": exp,
            "recency": rec,
            "penalty": penalty,
            "location_label": loc_label,
            "location_eligible": eligible,
            "walk_in": walk_in,
            "walk_in_eligible": walk_in_ok,
            "years_required": years,
            "years_max": years_max,
            "experience_eligible": exp_ok,
            "age_days": days,
        }
        return job


def age_days(job: Job, now: datetime | None = None) -> int | None:
    stamp = job.posted_date or job.first_seen
    if not stamp:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return max(0, (now - dt).days)
