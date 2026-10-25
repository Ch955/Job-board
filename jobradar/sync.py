"""Status merge rules shared with the app (docs/sync.js has the identical rules).

State: {"statuses":{id:{status,at,appliedAt?,source?}}, "saved":{id:job}, "sent":{id:iso},
        "pending":{id:{at,open}}, "gmail":{last_check, processed:[...], events:[...], review:[...]},
        "reviews":{review id:{job_id|null, at}}, "drafts":{key:{id, thread_id, job_id, type, at}}}
"""

from __future__ import annotations

KEEP = ("later", "applied", "interview", "offer", "rejected")
MAPS = ("statuses", "saved", "sent", "pending", "gmail", "reviews", "drafts", "walkins", "done")
NEWEST = ("pending", "reviews", "drafts", "walkins", "done")  # per key, the record with the later "at" wins


def normalize(s: dict | None) -> dict:
    s = dict(s or {})
    for k in MAPS:
        s[k] = dict(s.get(k) or {})
    return s


def merge(a: dict, b: dict) -> dict:
    """Per job the newest change wins; the first applied time is kept."""
    a, b = normalize(a), normalize(b)
    out = normalize({})
    for jid in set(a["statuses"]) | set(b["statuses"]):
        x, y = a["statuses"].get(jid), b["statuses"].get(jid)
        win = y if not x else x if not y else (y if str(y.get("at", "")) > str(x.get("at", "")) else x)
        applied = sorted(v for v in ((x or {}).get("appliedAt"), (y or {}).get("appliedAt")) if v)
        rec = dict(win)
        rec.pop("appliedAt", None)
        if applied:
            rec["appliedAt"] = applied[0]
        out["statuses"][jid] = rec
    for k in NEWEST:
        for key in set(a[k]) | set(b[k]):
            x, y = a[k].get(key), b[k].get(key)
            out[k][key] = y if not x else x if not y else (y if str(y.get("at", "")) > str(x.get("at", "")) else x)
    for jid in set(a["sent"]) | set(b["sent"]):
        out["sent"][jid] = max(v for v in (a["sent"].get(jid), b["sent"].get(jid)) if v)
    for jid in set(a["saved"]) | set(b["saved"]):
        if (out["statuses"].get(jid) or {}).get("status") in KEEP:
            out["saved"][jid] = b["saved"].get(jid) or a["saved"].get(jid)
    ga, gb = a["gmail"], b["gmail"]
    out["gmail"] = gb if str(gb.get("last_check", "")) >= str(ga.get("last_check", "")) else ga
    return out
