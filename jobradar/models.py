"""The one normalized job schema every source maps into."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

STATUSES = ("new", "applied", "skipped", "interview", "rejected", "offer")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|co|gmbh|technologies|technology|labs?)\b\.?"
)


def clean_text(value: str | None) -> str:
    """Strip HTML tags and entities, collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(value)
    # Greenhouse double-escapes its HTML content
    if "&lt;" in text or "&amp;" in text:
        text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _norm(value: str) -> str:
    value = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    return _WS_RE.sub(" ", value).strip()


def norm_company(company: str) -> str:
    return _WS_RE.sub(" ", _COMPANY_SUFFIX_RE.sub(" ", _norm(company))).strip()


def norm_location(location: str) -> str:
    """First meaningful token of a location so 'Bengaluru, KA, India' == 'Bangalore'."""
    loc = _norm(location)
    loc = loc.replace("bengaluru", "bangalore").replace("gurugram", "gurgaon")
    return loc.split(" ")[0] if loc else ""


def to_iso(value) -> str | None:
    """Accept datetime, epoch seconds/millis, or ISO-ish strings; return ISO-8601 UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
        else:
            s = str(value).strip().replace("Z", "+00:00")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                s += "T00:00:00+00:00"
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str
    remote: bool = False
    posted_date: str | None = None
    description_snippet: str = ""
    score: int = 0
    status: str = "new"
    matched_keywords: list[str] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    first_seen: str | None = None
    id: str = ""
    # Recruiter addresses found in the posting, and the Gmail draft made for it
    contact_emails: list[str] = field(default_factory=list)
    outreach: dict = field(default_factory=dict)
    # How to apply (apply_type.py): set from the source and the real application link; optional
    apply_url: str = ""
    apply_type: str = ""
    apply_evidence: str = ""
    work_mode: str = ""   # "remote" | "hybrid" | "onsite" when the listing says so, else ""
    # Full description is used for scoring only and never written to disk
    description: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        self.title = clean_text(self.title)
        self.company = clean_text(self.company)
        self.location = clean_text(self.location) or ("Remote" if self.remote else "")
        self.description = clean_text(self.description)
        self.posted_date = to_iso(self.posted_date)
        if not self.id:
            self.id = hashlib.sha1(self.dedupe_key.encode()).hexdigest()[:12]

    @property
    def dedupe_key(self) -> str:
        return "|".join((norm_company(self.company), _norm(self.title), norm_location(self.location)))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("description", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)
