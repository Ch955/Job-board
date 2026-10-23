"""The app (docs/sync.js) and GitHub Actions (jobradar/sync.py) must read each other's file
and merge identically."""

import json
import shutil
import subprocess

import pytest

from jobradar import sync
from jobradar.config import ROOT

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node not installed")


def js(req: dict):
    out = subprocess.run([NODE, str(ROOT / "tests/js/sync_cli.js")], input=json.dumps(req),
                         capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


STATE = {"statuses": {"a1": {"status": "applied", "at": "2026-10-23T10:00:00Z", "appliedAt": "2026-10-23T10:00:00Z"}},
         "saved": {"a1": {"id": "a1", "title": "Azure Data Engineer", "company": "Visa"}},
         "sent": {}, "pending": {"b2": {"at": "2026-10-23T09:00:00Z", "open": True}}, "gmail": {}}


def test_python_roundtrip_and_wrong_passphrase(tmp_path):
    salt = sync.b64u_encode(b"0123456789abcdef")
    key = sync.derive_key("correct horse", salt, 1000)
    env = sync.seal(STATE, key, salt, 1000)
    assert b"Visa" not in json.dumps(env).encode()
    state, _ = sync.open_envelope(env, "correct horse")
    assert state["statuses"]["a1"]["status"] == "applied"
    with pytest.raises(sync.WrongPassphrase):
        sync.open_envelope(env, "wrong")


@needs_node
def test_browser_file_opens_in_python_and_back():
    salt = sync.b64u_encode(b"fedcba9876543210")
    env = js({"op": "seal", "passphrase": "my pass phrase", "salt": salt, "iter": 2000, "state": STATE})
    state, key = sync.open_envelope(env, "my pass phrase")
    assert state["saved"]["a1"]["company"] == "Visa"
    back = sync.seal(state, key, salt, 2000)
    assert js({"op": "open", "passphrase": "my pass phrase", "envelope": back})["statuses"] == STATE["statuses"]


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
    # gmail bookkeeping: newer last_check wins
    ({"gmail": {"last_check": "2026-10-23T06:00:00Z", "processed": ["m1"]}},
     {"gmail": {"last_check": "2026-10-23T09:00:00Z", "processed": ["m1", "m2"]}}),
]


@needs_node
@pytest.mark.parametrize("a,b", MERGE_CASES)
def test_merge_matches_browser(a, b):
    assert sync.merge(a, b) == js({"op": "merge", "a": a, "b": b})
    assert sync.merge(b, a) == js({"op": "merge", "a": b, "b": a})  # order doesn't matter


def test_merge_rules():
    a, b = MERGE_CASES[1]
    m = sync.merge(a, b)
    assert m["statuses"]["j"] == {"status": "new", "at": "2026-10-23T09:05:00Z", "appliedAt": "2026-10-23T09:00:00Z"}
    assert m["saved"] == {}  # back in To apply: no snapshot needed
    assert sync.merge(*MERGE_CASES[2])["pending"]["p"]["open"] is False
