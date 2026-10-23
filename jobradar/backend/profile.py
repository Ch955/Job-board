"""A user's confirmed profile and preferences: validated before it's saved.

Stored at profiles/{uid}; the user can read it, only the API writes it (so these
checks always run). A phone number is optional everywhere.
"""

from __future__ import annotations

import re

from .. import skills_vocab

DEFAULT_THRESHOLD = 70
LIMITS = {"full_name": 80, "email": 120, "phone": 25, "linkedin": 120, "current_role": 80, "current_company": 80,
          "notice": 60, "location": 60}
LISTS = {"skills": (60, 40), "target_roles": (8, 60), "locations": (10, 40), "education": (6, 160),
         "certifications": (12, 140), "projects": (10, 200), "highlights": (3, 240), "degree_levels": (4, 20)}
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
            if k == "skills":
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
    for k, default in (("remote_ok", True), ("anywhere_in_india", False), ("drafts_enabled", True), ("notify", True)):
        v = raw.get(k, default)
        if not isinstance(v, bool):
            raise ProfileError(f"{k} must be true or false")
        out[k] = v
    return out


def is_complete(p: dict) -> list[str]:
    """What's still needed before matching can run (empty list = ready)."""
    missing = []
    if not p.get("skills"):
        missing.append("skills")
    if not (p.get("target_roles") or p.get("current_role")):
        missing.append("target role")
    return missing
