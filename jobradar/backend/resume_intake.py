"""Resume upload: validate, read the text, suggest profile facts, store encrypted.

Extraction only *suggests*: the app shows every field for the user to correct and
confirm, and only the confirmed profile is used for matching and drafts. The
original file is kept encrypted (bound to the uid) for draft attachments, and
the extracted text is never stored.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree

from .. import skills_vocab
from . import secretbox
from .store import Store

MAX_BYTES = 2 * 1024 * 1024  # resumes are rarely over 500 KB; keeps storage in the free tier
MAX_PAGES = 10
CHUNK = 700_000              # Firestore documents are limited to 1 MiB
MIME = {"pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class ResumeError(Exception):
    pass


# ---------------------------------------------------------------- validate
def kind_of(filename: str, data: bytes) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if not data:
        raise ResumeError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ResumeError(f"The file is over {MAX_BYTES // (1024 * 1024)} MB. Export a smaller PDF and try again.")
    if ext == "pdf" and data.startswith(b"%PDF"):
        return "pdf"
    if ext == "docx" and data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
    if ext == "doc":
        raise ResumeError("Old .doc files aren't supported. Save it as PDF or .docx.")
    raise ResumeError("Upload a PDF or .docx file.")


def safe_name(filename: str, kind: str) -> str:
    base = re.sub(r"[^A-Za-z0-9 ._-]+", "", filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip(" .") or "resume"
    base = base[:80]
    return base if base.lower().endswith("." + kind) else f"{base}.{kind}"


# -------------------------------------------------------------------- text
def text_of(kind: str, data: bytes) -> str:
    if kind == "pdf":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ResumeError("The PDF is password-protected. Upload an unprotected copy.")
            if len(reader.pages) > MAX_PAGES:
                raise ResumeError(f"The PDF has more than {MAX_PAGES} pages. Upload your resume only.")
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
        except PdfReadError as e:
            raise ResumeError("The PDF couldn't be read. Export it again and retry.") from e
    else:
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 20 * MAX_BYTES:  # zip bomb guard
                raise ResumeError("The document is too large.")
            root = ElementTree.fromstring(z.read(info))
        lines = []
        for para in root.iter(f"{ns}p"):
            lines.append("".join(t.text or "" for t in para.iter(f"{ns}t")))
        text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 200:
        raise ResumeError("No readable text found. If it's a scanned image, upload a text PDF or .docx.")
    return text


# ----------------------------------------------------------------- extract
SECTION = re.compile(r"^\s*(professional summary|summary|profile|objective|experience|work experience|professional experience|"
                     r"employment|education|academics?|qualifications?|certifications?|licen[cs]es|projects?|key projects|"
                     r"skills|technical skills|core skills|achievements|awards)\s*:?\s*$", re.I)
MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
DATE_RANGE = re.compile(rf"(?P<a>(?:{MONTHS}\.?\s+)?(?:19|20)\d\d)\s*(?:-|–|—|to)\s*(?P<b>present|current|now|till date|(?:{MONTHS}\.?\s+)?(?:19|20)\d\d)", re.I)
YEARS_STATED = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:\w+\s+){0,3}experience", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
PHONE = re.compile(r"\+?\d[\d\s().-]{8,16}\d")
LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", re.I)
ROLE = re.compile(r"\b((?:senior |junior |lead |associate |staff |principal )?(?:[a-z]+ ){0,2}"
                  r"(?:engineer|developer|analyst|scientist|consultant|architect|administrator|manager|designer))\b", re.I)
CERT = re.compile(r"certif|\b(az|dp|ai|pl|sc|ms)-\d{3}\b|\baws certified\b|\bpmp\b|\bscrum master\b", re.I)


def _sections(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"_top": []}
    cur = "_top"
    for line in lines:
        m = SECTION.match(line)
        if m:
            name = m.group(1).lower()
            cur = ("experience" if "experience" in name or name == "employment" else
                   "education" if name.startswith(("educ", "academ", "qualif")) else
                   "certifications" if name.startswith(("certif", "licen")) else
                   "projects" if "project" in name else
                   "skills" if "skill" in name else
                   "summary" if name in ("professional summary", "summary", "profile", "objective") else name)
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(line)
    return out


def _month_index(s: str) -> int:
    m = re.match(MONTHS, s.strip(), re.I)
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(1)[:3].lower()) if m else 0


def _years_from_ranges(text: str, now: datetime) -> float | None:
    spans = []
    for m in DATE_RANGE.finditer(text):
        a, b = m.group("a"), m.group("b")
        ya = int(re.search(r"(19|20)\d\d", a).group(0))
        start = ya * 12 + _month_index(a)
        if re.match(r"present|current|now|till", b, re.I):
            end = now.year * 12 + now.month - 1
        else:
            end = int(re.search(r"(19|20)\d\d", b).group(0)) * 12 + (_month_index(b) if re.match(MONTHS, b, re.I) else 11)
        if 0 < end - start < 12 * 45:
            spans.append((start, end))
    if not spans:
        return None
    spans.sort()
    total, (cs, ce) = 0, spans[0]
    for s, e in spans[1:]:  # merge overlapping jobs so they aren't counted twice
        if s <= ce:
            ce = max(ce, e)
        else:
            total, cs, ce = total + ce - cs, s, e
    total += ce - cs
    return round(total / 12, 1)


def extract(text: str, now: datetime | None = None) -> dict:
    """Suggested profile facts from resume text. Every field is for the user to confirm."""
    now = now or datetime.now(timezone.utc)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sec = _sections(lines)
    top = sec.get("_top", [])[:8]
    name = next((l for l in top if 2 <= len(l.split()) <= 4 and re.fullmatch(r"[A-Za-z .'-]+", l) and not SECTION.match(l)), "")
    email_m = EMAIL.search(text)
    email = email_m.group(0) if email_m else ""
    phone_m = next((m.group(0).strip() for m in PHONE.finditer("\n".join(top + lines[:20])) if 10 <= len(re.sub(r"\D", "", m.group(0))) <= 13 and not DATE_RANGE.search(m.group(0))), "")
    linkedin = (LINKEDIN.search(text).group(0) if LINKEDIN.search(text) else "")
    stated = [float(m.group(1)) for m in YEARS_STATED.finditer(text) if float(m.group(1)) <= 45]
    from_ranges = _years_from_ranges("\n".join(sec.get("experience", [])) or text, now)
    years = max(stated) if stated else from_ranges
    exp_lines = sec.get("experience", [])
    role = ""
    for l in (top + exp_lines[:6]):
        m = ROLE.search(l)
        if m and len(l) < 90:
            role = m.group(1).strip().title()
            break
    education = [l for l in sec.get("education", lines) if any(re.search(p, l, re.I) for p, _ in skills_vocab.DEGREES)][:4]
    degree_levels = sorted({lvl for p, lvl in skills_vocab.DEGREES for l in education if re.search(p, l, re.I)})
    certs = [l for l in (sec.get("certifications") or [l for l in lines if CERT.search(l)]) if len(l) < 140][:8]
    projects = [l.lstrip("•-*·● ") for l in sec.get("projects", []) if len(l) > 15][:8]
    skills = skills_vocab.find_skills(text)
    bullets = [l.lstrip("•-*·● ").strip() for l in exp_lines if l[:1] in "•-*·●" or len(l) > 60]
    highlights = sorted((b for b in bullets if 40 <= len(b) <= 220), key=lambda b: -len(skills_vocab.find_skills(b)))[:3]
    return {
        "full_name": name.title() if name.isupper() else name,
        "email": email,
        "phone": phone_m,
        "linkedin": linkedin,
        "years_experience": years,
        "years_source": "stated" if stated else "dates" if from_ranges is not None else None,
        "current_role": role,
        "skills": skills,
        "education": education,
        "degree_levels": degree_levels,
        "certifications": certs,
        "projects": projects,
        "highlights": highlights,
    }


# ----------------------------------------------------------------- storage
def save(store: Store, uid: str, filename: str, data: bytes, now: datetime | None = None) -> dict:
    """Validate, read and store one user's resume. Returns {file, suggested}."""
    kind = kind_of(filename, data)
    text = text_of(kind, data)
    name = safe_name(filename, kind)
    sealed = secretbox.seal(data, uid=uid, purpose="resume")
    parts = [sealed[i:i + CHUNK] for i in range(0, len(sealed), CHUNK)]
    old = store.get("resumes", uid) or {}
    for i, part in enumerate(parts):
        store.set("resume_chunks", f"{uid}_{i}", {"uid": uid, "i": i, "data": part})
    for i in range(len(parts), int(old.get("chunks", 0))):
        store.delete("resume_chunks", f"{uid}_{i}")
    meta = {"name": name, "mime": MIME[kind], "size": len(data), "chunks": len(parts),
            "uploaded": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    store.set("resumes", uid, meta)
    return {"file": meta, "suggested": extract(text, now)}


def load(store: Store, uid: str) -> tuple[str, bytes] | None:
    meta = store.get("resumes", uid)
    if not meta:
        return None
    sealed = "".join((store.get("resume_chunks", f"{uid}_{i}") or {}).get("data", "") for i in range(int(meta["chunks"])))
    return meta["name"], secretbox.open_(sealed, uid=uid, purpose="resume")


def delete(store: Store, uid: str) -> None:
    meta = store.get("resumes", uid) or {}
    for i in range(max(int(meta.get("chunks", 0)), 1) + 2):
        store.delete("resume_chunks", f"{uid}_{i}")
    store.delete("resumes", uid)
