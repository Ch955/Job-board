"""Read/write docs/jobs.json - the single data file the PWA reads."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import Job

SCHEMA_VERSION = 1


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": SCHEMA_VERSION, "jobs": [], "state": {}}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("jobs", [])
    data.setdefault("state", {})
    return data


def existing_jobs(data: dict) -> dict[str, Job]:
    return {j["id"]: Job.from_dict(j) for j in data.get("jobs", []) if j.get("id")}


def save(path: Path, data: dict) -> None:
    """Atomic write so a crashed run never leaves a half-written file for the PWA."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".jobs-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
