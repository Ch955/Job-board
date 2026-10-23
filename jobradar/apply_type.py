"""How you apply for a job, from evidence only: the source and the real application URL.

  easy_apply   the source's own data says you apply on the platform (no current source
               supplies this, so nothing is labelled Easy Apply without proof)
  company_site the link is the employer's careers portal (their own site or their ATS)
  external     the link goes through another identified provider (an aggregator)
  unknown      not established: a job-board page whose apply method we can't see

docs/app.js has the same rules for records saved before this field existed.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

TYPES = ("easy_apply", "company_site", "external", "unknown")
# Applicant-tracking systems host employers' own career portals
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "workday.com",
             "icims.com", "taleo.net", "successfactors.com", "jobvite.com", "zohorecruit.com", "recruitee.com",
             "workable.com", "bamboohr.com", "darwinbox.in", "keka.com", "freshteam.com", "oraclecloud.com",
             "breezy.hr", "teamtailor.com", "personio.de", "personio.com")
BOARDS = {"linkedin.com": "LinkedIn", "naukri.com": "Naukri", "indeed.com": "Indeed", "indeed.co.in": "Indeed",
          "glassdoor.com": "Glassdoor", "glassdoor.co.in": "Glassdoor", "foundit.in": "foundit", "monster.com": "Monster"}
EMPLOYER_BOARDS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "smartrecruiters": "SmartRecruiters"}
AGGREGATORS = {"adzuna": "Adzuna", "jooble": "Jooble", "remotive": "Remotive", "arbeitnow": "Arbeitnow"}


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _on(host: str, domains) -> str | None:
    return next((d for d in domains if host == d or host.endswith("." + d)), None)


def classify(source: str, url: str, apply_url: str = "", easy_apply: bool | None = None) -> dict:
    """{"apply_type", "apply_url", "apply_evidence"} for one job."""
    link = apply_url or url
    host = _host(link)
    if easy_apply is True:
        return {"apply_type": "easy_apply", "apply_url": url, "apply_evidence": "The source marks this as apply-on-platform"}
    if not host:
        return {"apply_type": "unknown", "apply_url": url or "", "apply_evidence": "No application link"}
    if source in EMPLOYER_BOARDS:  # fetched from the employer's own board on that ATS (link may be a custom domain)
        return {"apply_type": "company_site", "apply_url": link,
                "apply_evidence": f"Employer's careers portal ({EMPLOYER_BOARDS[source]})"}
    ats = _on(host, ATS_HOSTS)
    if ats:
        return {"apply_type": "company_site", "apply_url": link, "apply_evidence": f"Employer's careers portal ({ats})"}
    if source in AGGREGATORS:
        return {"apply_type": "external", "apply_url": link,
                "apply_evidence": f"Opens through {AGGREGATORS[source]}, which forwards to the employer or another site"}
    board = _on(host, BOARDS)
    if board:
        return {"apply_type": "unknown", "apply_url": link,
                "apply_evidence": f"{BOARDS[board]} page; the apply method isn't shown in the listing data"}
    if apply_url:  # a direct link that isn't a job board or ATS: the employer's own site
        return {"apply_type": "company_site", "apply_url": link, "apply_evidence": f"Direct link to {host}"}
    return {"apply_type": "unknown", "apply_url": link, "apply_evidence": "Apply method not established"}


def work_mode(remote: bool, text: str) -> str:
    """"remote" | "hybrid" | "onsite" only when the listing says so; "" when it doesn't."""
    if remote:
        return "remote"
    t = text or ""
    if re.search(r"\bhybrid\b", t, re.I):
        return "hybrid"
    if re.search(r"work(ing)? from (the )?office|\bon[- ]?site\b|\bin[- ]office\b|\bwfo\b", t, re.I):
        return "onsite"
    return ""


def annotate(job) -> None:
    """Fill a Job's apply_type / apply_url / apply_evidence / work_mode (in place)."""
    direct = getattr(job, "apply_url", "") or ""
    direct = direct if direct != job.url else ""  # a previous annotate copied the listing URL: not a direct link
    info = classify(job.source, job.url, direct, getattr(job, "easy_apply", None))
    job.apply_type, job.apply_url, job.apply_evidence = info["apply_type"], info["apply_url"], info["apply_evidence"]
    job.work_mode = work_mode(job.remote, f"{job.title} {job.location} {job.description or job.description_snippet}")
