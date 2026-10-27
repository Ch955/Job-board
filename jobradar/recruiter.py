"""A recruiter's name for an email address, only from evidence. Never guessed.

Sources, best first:
  posting  the job post names the person next to the address ("Share your CV with Priya Sharma –
           priya.sharma@acme.com", "Contact: Priya Sharma", "Regards, Priya Sharma")
  gmail    the address has emailed you before: the display name it sends under (looked up in the
           user's own Gmail by the caller)
  address  the address itself is clearly a person's name: priya.sharma@acme.com -> Priya Sharma
           (hr@, careers@, talent.team@ and other role addresses never count)
"""

from __future__ import annotations

import re

ROLE_WORDS = {
    "hr", "careers", "career", "jobs", "job", "talent", "recruitment", "recruiter", "recruiters", "recruiting", "hiring",
    "info", "contact", "contacts", "resume", "resumes", "cv", "cvs", "apply", "admin", "team", "people", "office", "mail",
    "enquiry", "enquiries", "hello", "support", "staffing", "jobsindia", "india", "noreply", "no", "reply", "placement",
    "placements", "opportunities", "work", "join", "joinus", "hrd", "hrteam", "ta", "humanresources", "human", "resources",
    "sales", "marketing", "accounts", "billing", "connect", "manager", "head", "lead", "desk", "dept", "group",
}
NOT_NAMES = {
    "data", "engineer", "engineers", "developer", "team", "hiring", "hr", "human", "resources", "talent", "acquisition",
    "please", "interested", "candidates", "candidate", "email", "mail", "contact", "share", "send", "resume", "cv", "profile",
    "india", "hyderabad", "bangalore", "bengaluru", "pune", "chennai", "mumbai", "delhi", "noida", "gurgaon", "remote",
    "regards", "thanks", "thank", "you", "best", "warm", "the", "and", "at", "to", "for", "with", "us", "our", "apply",
    "immediate", "joiners", "joiner", "notice", "period", "location", "experience", "years", "role", "job", "opening",
    "manager", "lead", "senior", "junior", "recruiter", "recruitment", "company", "technologies", "solutions", "services",
    "pvt", "ltd", "limited", "inc", "azure", "aws", "sql", "python", "spark", "databricks", "note", "kindly", "hi", "hello",
    "dear", "sir", "madam", "mr", "ms", "mrs", "whatsapp", "call", "phone", "mobile", "number",
}
NAME = r"([A-Z][a-z]{1,20}(?:[ .][A-Z][a-z]{1,20}){0,2})"
CUE = (r"(?:contact(?: person)?|reach out to|write to|connect with|share (?:your |updated )?(?:cv|resume|profile)s? (?:with|to)|"
       r"send (?:your |updated )?(?:cv|resume|profile)s? to|regards|thanks|thank you|recruiter|hr|talent acquisition|poc|spoc)")


def _clean(name: str) -> str:
    words = [w for w in re.split(r"[ .]+", name.strip()) if w]
    if not words or len(words) > 3 or any(w.lower() in NOT_NAMES for w in words):
        return ""
    return " ".join(words)


def name_near(text: str, email: str) -> str:
    """The person the posting names next to `email`, or ""."""
    if not text or not email:
        return ""
    for m in re.finditer(re.escape(email), text, re.I):
        before = text[max(0, m.start() - 160):m.start()]
        after = text[m.end():m.end() + 80]
        # "Priya Sharma - priya@x.com", "Priya Sharma (priya@x.com)", "Priya Sharma: priya@x.com"
        hit = re.search(NAME + r"\s*[(\[<:\-–|,]*\s*(?:email|e-mail|mail id|mail)?\s*[:\-–]?\s*$", before)
        if hit and _clean(hit.group(1)):
            return _clean(hit.group(1))
        # "Contact: Priya Sharma at priya@x.com", "share your resume with Priya Sharma, priya@x.com"
        hit = re.search(rf"(?i:{CUE})[\s:,\-–]*(?:(?i:mr|ms|mrs)\.?\s+)?{NAME}[\s,:\-–(]*(?:(?i:at|on|via|email|e-mail|mail)\s*[:\-–]?\s*)?$", before)
        if hit and _clean(hit.group(1)):
            return _clean(hit.group(1))
        # "priya@x.com (Priya Sharma)", "priya@x.com - Priya Sharma, HR"
        hit = re.match(r"\s*[(\[\-–|,]\s*" + NAME, after)
        if hit and _clean(hit.group(1)):
            return _clean(hit.group(1))
    # "Regards,\nPriya Sharma" anywhere in a short post that also has this address
    hit = re.search(rf"(?i:regards|thanks|thank you)[,!.]?\s*\n?\s*{NAME}\b", text)
    if hit and _clean(hit.group(1)) and _matches_address(_clean(hit.group(1)), email):
        return _clean(hit.group(1))
    return ""


def _matches_address(name: str, email: str) -> bool:
    local = re.sub(r"[^a-z]", "", email.split("@")[0].lower())
    return any(len(w) >= 3 and w.lower() in local for w in name.split())


def name_from_address(email: str) -> str:
    """priya.sharma@acme.com -> "Priya Sharma"; hr@ / careers@ / anything not clearly a name -> ""."""
    local = re.sub(r"\d+", "", (email or "").split("@")[0].lower())
    parts = [p for p in re.split(r"[._-]+", local) if p]
    if len(parts) not in (2, 3) or not all(p.isalpha() for p in parts):
        return ""
    if any(p in ROLE_WORDS or p in NOT_NAMES for p in parts) or len(parts[0]) < 3 or all(len(p) == 1 for p in parts[1:]):
        return ""
    return " ".join(p.capitalize() for p in parts if len(p) > 1) or ""


def display_name(from_header: str, email: str) -> str:
    """The name in an email's From header ("Priya Sharma <priya@x.com>"), if it's a person's name."""
    m = re.match(r'\s*"?([^"<]+?)"?\s*<([^>]+)>', from_header or "")
    if not m or m.group(2).strip().lower() != email.lower():
        return ""
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    if "@" in name or any(w.lower() in ROLE_WORDS or w.lower() in NOT_NAMES for w in name.split()):
        return ""
    words = name.split()
    return name if 1 <= len(words) <= 3 and all(re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", w) for w in words) else ""


def people_in(text: str, emails: list[str]) -> list[dict]:
    """[{email, name, source}] for addresses in a posting, with the posting's name for each when it gives one."""
    out = []
    for e in emails or []:
        name = name_near(text, e)
        out.append({"email": e, "name": name, "source": "posting" if name else ""})
    return out
