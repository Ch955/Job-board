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

        loc = config["location"]
        self.loc_cfg = loc
        self.preferred = {city: [_rx(p) for p in pats] for city, pats in loc["preferred"].items()}
        self.india = [_rx(p) for p in loc.get("india_terms", [])]
        self.not_india = [_rx(p) for p in loc.get("not_india_terms", [])]
        self.remote_terms = [_rx(p) for p in loc.get("remote_terms", [])]

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
    def required_years(text: str) -> int | None:
        """Smallest 'N years' requirement mentioned near the word experience."""
        found = []
        for rx in (_YEARS_RANGE, _YEARS_MIN):
            for m in rx.finditer(text):
                window = text[max(0, m.start() - 60) : m.end() + 60]
                n = int(m.group(1))
                if n <= 20 and _EXPERIENCE_NEAR.search(window):
                    found.append(n)
        return min(found) if found else None

    def experience_points(self, text: str) -> tuple[int, int | None]:
        years = self.required_years(text)
        full = int(self.exp["points"])
        if years is None:
            return int(self.exp.get("unknown_points", full // 2)), None
        if years <= int(self.exp["full_if_min_at_most"]):
            return full, years
        if years <= int(self.exp["half_if_min_at_most"]):
            return full // 2, years
        return 0, years

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
        penalty = self.title_penalty(job.title)
        skills, matched = self.skill_points(text)
        loc, loc_label, eligible = self.location_points(job)
        exp, years = self.experience_points(job.description)
        rec, days = self.recency_points(job, now)

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
            "years_required": years,
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
