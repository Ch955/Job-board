"""A user's confirmed profile and preferences: validated before it's saved.

Stored at profiles/{uid}; the user can read it, only the API writes it (so these
checks always run). A phone number is optional everywhere.
"""

from __future__ import annotations

import re

from .. import skills_vocab

DEFAULT_THRESHOLD = 70
LIMITS = {"full_name": 80, "email": 120, "phone": 25, "linkedin": 120, "current_role": 80, "current_company": 80,
          "notice": 60, "location": 60, "current_ctc": 40, "expected_ctc": 40, "portfolio": 160}
WORK_MODES = ("onsite", "hybrid", "remote")
SCOPES = ("selected", "india")           # only my cities / anywhere in India
RELOCATION = ("yes", "no", "flexible")
LISTS = {"skills": (60, 40), "target_roles": (8, 60), "locations": (10, 40), "education": (6, 160),
         "work_modes": (3, 10), "walkin_cities": (10, 40), "walkin_roles": (8, 60), "walkin_industries": (8, 40),
         "certifications": (12, 140), "projects": (10, 200), "highlights": (3, 240), "degree_levels": (4, 20),
         "added_skills": (60, 40), "hidden_skills": (200, 40)}  # added from the Skill match list / hidden from it
EMAIL = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[A-Za-z]{2,24}")


class ProfileError(ValueError):
    pass


def _num(v, lo, hi, name, default=None):
    if v in (None, ""):
        return default
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ProfileError(f"{name} must be a number") from None
    if not lo <= n <= hi:
        raise ProfileError(f"{name} must be between {lo} and {hi}")
    return int(n) if n.is_integer() else n


def clean(raw: dict) -> dict:
    """Validated copy of what the user confirmed. Unknown fields are dropped."""
    if not isinstance(raw, dict):
        raise ProfileError("profile must be an object")
    out: dict = {}
    for k, n in LIMITS.items():
        v = raw.get(k)
        if v not in (None, ""):
            if not isinstance(v, str):
                raise ProfileError(f"{k} must be text")
            v = re.sub(r"\s+", " ", v).strip()
            if len(v) > n:
                raise ProfileError(f"{k} is too long (max {n})")
            out[k] = v
    if out.get("email") and not EMAIL.fullmatch(out["email"]):
        raise ProfileError("email doesn't look valid")
    if out.get("phone") and len(re.sub(r"\D", "", out["phone"])) < 7:
        raise ProfileError("phone number looks incomplete (or leave it empty, it's optional)")
    for k, (count, n) in LISTS.items():
        v = raw.get(k) or []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ProfileError(f"{k} must be a list of text")
        items, seen = [], set()
        for x in v:
            x = re.sub(r"\s+", " ", x).strip()
            if k in ("skills", "added_skills", "hidden_skills"):
                x = skills_vocab.canonical(x)
            if x and x.lower() not in seen and len(x) <= n:
                seen.add(x.lower())
                items.append(x)
        if len(items) > count:
            raise ProfileError(f"too many {k} (max {count})")
        out[k] = items
    out["years_experience"] = _num(raw.get("years_experience"), 0, 50, "years_experience")
    out["min_years"] = _num(raw.get("min_years"), 0, 50, "min_years")
    out["max_years"] = _num(raw.get("max_years"), 0, 50, "max_years")
    if out["min_years"] is not None and out["max_years"] is not None and out["min_years"] > out["max_years"]:
        raise ProfileError("min_years can't be above max_years")
    out["threshold"] = int(_num(raw.get("threshold"), 0, 100, "threshold", DEFAULT_THRESHOLD))
    # Job preferences. Older profiles had only anywhere_in_india / remote_ok: those still decide the defaults.
    if any(m not in WORK_MODES for m in out["work_modes"]):
        raise ProfileError("work_modes can be onsite, hybrid and/or remote")
    if not out["work_modes"]:
        out["work_modes"] = list(WORK_MODES) if raw.get("remote_ok", True) is not False else ["onsite", "hybrid"]
    scope = raw.get("search_scope") or ("india" if raw.get("anywhere_in_india") or not out["locations"] else "selected")
    if scope not in SCOPES:
        raise ProfileError("search_scope must be 'selected' or 'india'")
    out["search_scope"] = scope
    reloc = raw.get("relocation") or "flexible"
    if reloc not in RELOCATION:
        raise ProfileError("relocation must be yes, no or flexible")
    out["relocation"] = reloc
    # Walk-ins: separate cities (you have to be there in person), date window, radius
    out["walkin_radius_km"] = _num(raw.get("walkin_radius_km"), 0, 1000, "walkin_radius_km")
    out["walkin_min_score"] = int(_num(raw.get("walkin_min_score"), 0, 100, "walkin_min_score", 50))
    out["walkin_min_years"] = _num(raw.get("walkin_min_years"), 0, 50, "walkin_min_years")
    out["walkin_max_years"] = _num(raw.get("walkin_max_years"), 0, 50, "walkin_max_years")
    for k in ("walkin_from", "walkin_to"):
        v = raw.get(k) or None
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
            raise ProfileError(f"{k} must be a date (YYYY-MM-DD)")
        out[k] = v
    if out["walkin_from"] and out["walkin_to"] and out["walkin_from"] > out["walkin_to"]:
        raise ProfileError("walk-in 'from' date is after the 'to' date")
    for k, default in (("remote_ok", True), ("anywhere_in_india", False), ("drafts_enabled", True), ("notify", True),
                       ("walkin_same_as_jobs", False)):
        v = raw.get(k, default)
        if not isinstance(v, bool):
            raise ProfileError(f"{k} must be true or false")
        out[k] = v
    # Keep the older switches in step with the new preferences (read by older app versions)
    out["remote_ok"] = "remote" in out["work_modes"]
    out["anywhere_in_india"] = out["search_scope"] == "india"
    have = {x.lower() for x in out["skills"]}
    out["added_skills"] = [x for x in out["added_skills"] if x.lower() in have]
    if out["walkin_same_as_jobs"]:
        out["walkin_cities"] = list(out["locations"])
    return out


def is_complete(p: dict) -> list[str]:
    """What's still needed before matching can run (empty list = ready)."""
    missing = []
    if not p.get("skills"):
        missing.append("skills")
    if not (p.get("target_roles") or p.get("current_role")):
        missing.append("target role")
    return missing
