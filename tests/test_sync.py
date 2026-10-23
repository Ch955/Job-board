"""The app (docs/sync.js) and GitHub Actions (jobradar/sync.py) must merge identically."""

import json
import shutil
import subprocess

import pytest

from jobradar import sync
from jobradar.config import ROOT

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node not installed")


def js_merge(a, b):
    out = subprocess.run([NODE, str(ROOT / "tests/js/sync_cli.js")], input=json.dumps({"a": a, "b": b}),
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


MERGE_CASES = [
    # phone marked applied later than laptop's skip -> applied wins, applied time kept
    ({"statuses": {"j": {"status": "skipped", "at": "2026-10-23T08:00:00Z"}}},
     {"statuses": {"j": {"status": "applied", "at": "2026-10-23T09:00:00Z", "appliedAt": "2026-10-23T09:00:00Z"}},
      "saved": {"j": {"id": "j"}}}),
    # undo on one device (newer "new") beats the older applied; first applied time survives
    ({"statuses": {"j": {"status": "applied", "at": "2026-10-23T09:00:00Z", "appliedAt": "2026-10-23T09:00:00Z"}},
      "saved": {"j": {"id": "j"}}},
     {"statuses": {"j": {"status": "new", "at": "2026-10-23T09:05:00Z"}}}),
    # pending opened on phone, answered "not yet" on laptop later
    ({"pending": {"p": {"at": "2026-10-23T09:00:00Z", "open": True}}},
     {"pending": {"p": {"at": "2026-10-23T09:10:00Z", "open": False}}, "sent": {"x": "2026-10-23T01:00:00Z"}}),
    # review answered on phone; a draft recorded by the server on each side
    ({"reviews": {"r-m1": {"job_id": None, "at": "2026-10-23T09:00:00Z"}}, "drafts": {"apply:j": {"id": "d1", "at": "2026-10-23T07:00:00Z"}}},
     {"reviews": {"r-m1": {"job_id": "j2", "at": "2026-10-23T09:30:00Z"}}, "drafts": {"reply:t1": {"id": "d2", "at": "2026-10-23T08:00:00Z"}}}),
    # a walk-in saved on the phone, unsaved later on the laptop
    ({"walkins": {"w-1": {"saved": True, "at": "2026-10-23T09:00:00Z"}}},
     {"walkins": {"w-1": {"saved": False, "at": "2026-10-23T10:00:00Z"}, "w-2": {"saved": True, "at": "2026-10-23T08:00:00Z"}}}),
    # gmail bookkeeping: newer last_check wins
    ({"gmail": {"last_check": "2026-10-23T06:00:00Z", "processed": ["m1"]}},
     {"gmail": {"last_check": "2026-10-23T09:00:00Z", "processed": ["m1", "m2"]}}),
]


@needs_node
@pytest.mark.parametrize("a,b", MERGE_CASES)
def test_merge_matches_browser(a, b):
    assert sync.merge(a, b) == js_merge(a, b)
    assert sync.merge(b, a) == js_merge(b, a)  # order doesn't matter


def test_merge_rules():
    a, b = MERGE_CASES[1]
    m = sync.merge(a, b)
    assert m["statuses"]["j"] == {"status": "new", "at": "2026-10-23T09:05:00Z", "appliedAt": "2026-10-23T09:00:00Z"}
    assert m["saved"] == {}  # back in To apply: no snapshot needed
    assert sync.merge(*MERGE_CASES[2])["pending"]["p"]["open"] is False
