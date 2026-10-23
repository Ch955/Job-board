"""Per-user Gmail drafts, written from that user's own confirmed profile. Never sent.

Every fact in a draft comes from the user's profile (what they confirmed after
uploading their resume) or from the job / email being answered. A sentence whose
fact is missing is left out; nothing is guessed. Answers only the user can give
(salary, dates) are left as [brackets] for them to fill in before sending.

Each draft is recorded in the user's state `drafts[key]` (key = type + job or
thread), so the same draft is never created twice, from any device.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import gmail

TYPES = {
    "application": "Application",
    "outreach": "Recruiter outreach",
    "follow_up": "Follow-up",
    "reply": "Reply to recruiter",
    "scheduling": "Interview scheduling",
    "confirmation": "Interview confirmation",
    "thank_you": "Thank-you after interview",
    "offer": "Offer response",
}
REPLIES = {"reply", "scheduling", "confirmation", "thank_you", "offer"}  # go into the recruiter's thread
ATTACH = {"application", "outreach", "follow_up"}                     # carry the resume


class DraftError(Exception):
    pass


def _skills_for(job: dict, profile: dict, n: int = 3) -> list[str]:
    mine = {s.lower(): s for s in profile.get("skills") or []}
    wanted = [k for k in job.get("matched_keywords") or job.get("matched_skills") or [] if k.lower() in mine]
    if not wanted:  # fall back to the posting text, still only your skills
        text = f"{job.get('title', '')} {job.get('description_snippet', '')}".lower()
        wanted = [v for k, v in mine.items() if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", text)]
    return [mine.get(k.lower(), k) for k in wanted if k.lower() not in ("sql", "python")][:n] or wanted[:n]


def _join(lines) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in lines if l is not None)).strip() + "\n"


def compose(kind: str, profile: dict, job: dict | None = None, reply_to: dict | None = None,
            attached: bool = True) -> tuple[str, str]:
    """(subject, body). `reply_to` = {"subject", "from_name"} of the email being answered;
    `attached` = the resume goes with it (otherwise the draft doesn't say it does)."""
    if kind not in TYPES:
        raise DraftError(f"Unknown draft type: {kind}")
    name = (profile.get("full_name") or "").strip()
    if not name or not profile.get("email"):
        raise DraftError("Add your name and email in your profile first.")
    job = job or {}
    title, company = job.get("title") or "", job.get("company") or ""
    role, years = profile.get("current_role") or "", profile.get("years_experience")
    skills = _skills_for(job, profile)
    hi = f"Hi {reply_to['from_name'].split()[0]}," if reply_to and reply_to.get("from_name") and "@" not in reply_to["from_name"] else "Hi,"
    years_txt = f"{years:g}" if isinstance(years, (int, float)) else str(years or "")

    intro = None
    if role and years_txt:
        intro = f"I'm a {role} with {years_txt} years of experience" + (f" at {profile['current_company']}" if profile.get("current_company") else "") + "."
    elif role:
        intro = f"I'm a {role}" + (f" at {profile['current_company']}" if profile.get("current_company") else "") + "."
    fit = f"My hands-on work with {', '.join(skills)} matches what this role needs." if skills else None
    highlight = (profile.get("highlights") or [None])[0]
    notice = profile.get("notice")
    notice_line = f"Notice period: {notice}." if notice else None
    sign = ["Thanks,", name, profile.get("email"), profile.get("phone") or None, profile.get("linkedin") or None]
    where = f" at {company}" if company else ""
    re_subject = (reply_to or {}).get("subject") or (f"{title}{where}" if title else "Your message")
    re_subject = re_subject if re_subject.lower().startswith("re:") else f"Re: {re_subject}"

    if kind == "application":
        subject = f"Application: {title}{where} - {name}" if title else f"Application - {name}"
        body = [hi, "", f"I'd like to apply for the {title} role{where}." if title else "I'd like to be considered for a role on your team.",
                "", " ".join(x for x in (intro, fit) if x) or None, highlight, notice_line, "",
                ("My resume is attached. " if attached else "") + "I'd be glad to talk whenever it suits you.", "", *sign]
    elif kind == "outreach":
        subject = f"{title}{where} - {name}" if title else f"Data roles{where} - {name}"
        body = [hi, "", f"I saw the {title} opening{where} and wanted to reach out directly." if title else
                "I'm reaching out about open roles on your team.", "", " ".join(x for x in (intro, fit) if x) or None,
                notice_line, "", ("My resume is attached. " if attached else "") + "Would you be open to a short call?", "", *sign]
    elif kind == "follow_up":
        subject = f"Following up: {title}{where}" if title else "Following up on my application"
        body = [hi, "", f"I applied for the {title} role{where} and wanted to follow up." if title else
                "I wanted to follow up on my application.",
                "I'm still very interested and happy to share anything else you need.", notice_line, "",
                "My resume is attached again for convenience." if attached else None, "", *sign]
    elif kind == "reply":
        subject = re_subject
        answers = [f"- Notice period: {notice}" if notice else "- Notice period: [your notice period]",
                   f"- Current location: {profile['location']}" if profile.get("location") else "- Current location: [your city]",
                   f"- Total experience: {years_txt} years" if years_txt else "- Total experience: [years]",
                   "- Current CTC: [fill in]", "- Expected CTC: [fill in]"]
        body = [hi, "", "Thank you for reaching out" + (f" about the {title} role" if title else "") + ". Here are the details:",
                "", *answers, "", "Happy to answer anything else.", "", *sign]
    elif kind == "scheduling":
        subject = re_subject
        body = [hi, "", "Thank you for the update" + (f" on the {title} role" if title else "") + ". I'm available at these times (IST):",
                "", "- [day, date, time]", "- [day, date, time]", "", "Please let me know what works best.", "", *sign]
    elif kind == "confirmation":
        subject = re_subject
        body = [hi, "", "Thank you. I confirm the interview at [day, date, time IST]" + (f" for the {title} role" if title else "") + ".",
                "Please let me know if anything is needed from my side beforehand.", "", *sign]
    elif kind == "thank_you":
        subject = re_subject
        body = [hi, "", "Thank you for your time today" + (f" discussing the {title} role{where}" if title else "") + ".",
                "I enjoyed the conversation and I'm keen on the next steps.", "", *sign]
    else:  # offer
        subject = re_subject
        body = [hi, "", "Thank you for the offer" + (f" for the {title} role{where}" if title else "") + ". I'm reviewing the details and will confirm by [date].",
                "[Add any questions about the offer here.]", "", *sign]
    return subject, _join(body)


def key_for(kind: str, job_id: str | None = None, thread_id: str | None = None) -> str:
    return f"{kind}:{thread_id if kind in REPLIES and thread_id else job_id}"


def create(client: gmail.GmailClient, state: dict, profile: dict, kind: str, *, job: dict | None = None,
           to: str = "", reply_to_message: str | None = None, resume: tuple[str, bytes] | None = None,
           now: datetime | None = None) -> dict:
    """Create one draft in this user's Gmail and record it. Returns the record (existing one if a duplicate)."""
    if profile.get("drafts_enabled") is False:
        raise DraftError("Drafts are turned off in your settings.")
    job = job or {}
    thread_id = in_reply_to = None
    reply_ctx = None
    if kind in REPLIES:
        if not reply_to_message:
            raise DraftError("Choose the email to reply to.")
        m = client.message(reply_to_message)
        hdr = {h["name"].lower(): h["value"] for h in (m.get("payload") or {}).get("headers") or []}
        sender = hdr.get("from", "")
        addr = re.search(r"<([^>]+)>", sender)
        to = to or (addr.group(1) if addr else sender.strip())
        thread_id, in_reply_to = m.get("threadId"), hdr.get("message-id")
        name_m = re.match(r'\s*"?([^"<]+?)"?\s*<', sender)
        reply_ctx = {"subject": hdr.get("subject", ""), "from_name": name_m.group(1) if name_m else ""}
    key = key_for(kind, job.get("id"), thread_id)
    drafts = state.setdefault("drafts", {})
    if key in drafts and drafts[key].get("id"):
        return {**drafts[key], "duplicate": True}
    if not to or not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[a-z]{2,24}", to, re.I):
        raise DraftError("No recruiter email address for this draft.")
    attachment = resume if kind in ATTACH else None
    subject, body = compose(kind, profile, job, reply_ctx, attached=bool(attachment))
    d = client.create_draft(to=to, subject=subject, body=body, attachment=attachment, sender=profile.get("email"),
                            thread_id=thread_id, in_reply_to=in_reply_to)
    rec = {"id": d["id"], "thread_id": d.get("thread_id") or thread_id or "", "job_id": job.get("id"), "type": kind,
           "to": to, "subject": subject[:140], "attached": bool(attachment),
           "at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    drafts[key] = rec
    return rec
