"""Web Push: encryption round-trip (decrypting as the browser would), VAPID JWT, sending."""

import json
import os
import struct
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jobradar import notify, push
from jobradar.models import Job


def browser_subscription():
    """What a browser's PushManager.subscribe() hands back, plus its private key."""
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
           "keys": {"p256dh": push.b64u_encode(push._public_bytes(ua.public_key())), "auth": push.b64u_encode(auth)}}
    return sub, ua, auth


def browser_decrypt(body: bytes, ua: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """Independent implementation of the receiving side of RFC 8291."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    ua_public = push._public_bytes(ua.public_key())
    ecdh = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = push._hmac(push._hmac(auth, ecdh), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = push._hmac(salt, ikm)
    cek = push._hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = push._hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


def test_encrypt_roundtrip():
    sub, ua, auth = browser_subscription()
    msg = "92 · Azure Data Engineer — Visa · Hyderabad".encode()
    body = push.encrypt(msg, sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert browser_decrypt(body, ua, auth) == msg


def test_payload_size_limit():
    sub, _, _ = browser_subscription()
    with pytest.raises(ValueError):
        push.encrypt(b"x" * 5000, sub["keys"]["p256dh"], sub["keys"]["auth"])


def test_vapid_keys_and_jwt_verify():
    private, public = push.generate_vapid_keys()
    assert push.public_key_for(private) == public and len(push.b64u_decode(public)) == 65
    header = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc", private, "mailto:me@example.com")
    token = header.split("t=")[1].split(",")[0]
    k = header.split("k=")[1]
    assert k == public
    h, c, sig = token.split(".")
    claims = json.loads(push.b64u_decode(c))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "mailto:me@example.com"
    raw = push.b64u_decode(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.b64u_decode(public))
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if invalid


class FakeResp:
    def __init__(self, code): self.status_code = code
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, code=201):
        self.code, self.posts = code, []
    def post(self, url, data=None, headers=None):
        self.posts.append((url, data, headers))
        return FakeResp(self.code)


def test_send_headers_and_body():
    sub, ua, auth = browser_subscription()
    private, _ = push.generate_vapid_keys()
    s = FakeSession()
    assert push.send(sub, {"title": "hi"}, private, "mailto:a@b.c", s) == 201
    url, body, headers = s.posts[0]
    assert url == sub["endpoint"] and headers["Content-Encoding"] == "aes128gcm"
    assert headers["Authorization"].startswith("vapid t=")
    assert json.loads(browser_decrypt(body, ua, auth)) == {"title": "hi"}


def test_send_gone():
    sub, _, _ = browser_subscription()
    private, _ = push.generate_vapid_keys()
    with pytest.raises(push.PushGone):
        push.send(sub, {"title": "hi"}, private, "mailto:a@b.c", FakeSession(410))


# -------------------------------------------------------------------- notify
def test_load_subscriptions_formats():
    a, _, _ = browser_subscription()
    b, _, _ = browser_subscription()
    assert len(notify.load_subscriptions(json.dumps(a))) == 1
    assert len(notify.load_subscriptions(json.dumps([a, b]))) == 2
    assert len(notify.load_subscriptions(json.dumps(a) + "\n\n" + json.dumps(b) + "\n")) == 2
    assert notify.load_subscriptions("") == []


def _job(title, score):
    j = Job(title=title, company="Acme", location="Pune", url="https://x/" + title, source="s")
    j.score, j.matched_keywords, j.score_breakdown = score, ["ADF", "Databricks"], {"location_label": "Pune"}
    return j


def test_alert_payloads_cap_and_rollup():
    jobs = [_job(f"Data Engineer {i}", 70 + i) for i in range(8)] + [_job("Weak", 50)]
    p = notify.alert_payloads(jobs, 70, max_individual=5)
    assert len(p) == 6
    assert p[0]["title"].startswith("77 ·") and p[0]["job_id"]
    assert p[-1]["title"] == "+3 more strong matches"
    assert notify.alert_payloads([_job("Weak", 50)], 70) == []


def test_summary_payload():
    now = datetime(2026, 10, 23, 3, 30, tzinfo=timezone.utc)
    recent, old = (now - timedelta(hours=5)).isoformat(), (now - timedelta(days=3)).isoformat()
    jobs = [{"title": "A", "company": "X", "score": 90, "first_seen": recent},
            {"title": "B", "company": "Y", "score": 50, "first_seen": recent},
            {"title": "C", "company": "Z", "score": 95, "first_seen": old}]
    p = notify.summary_payload(jobs, 70, now)
    assert p["title"] == "Job Radar · 2 new jobs today" and "90 · A @ X" in p["body"]
    assert "No new matches" in notify.summary_payload([], 70, now)["body"]


def test_send_payloads(monkeypatch):
    sub, ua, auth = browser_subscription()
    private, _ = push.generate_vapid_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    monkeypatch.setenv("WEBPUSH_SUBSCRIPTIONS", json.dumps(sub))
    s = FakeSession()
    report = notify.send_payloads([{"title": "1"}, {"title": "2"}], "mailto:a@b.c", s)
    assert report == {"sent": 2, "failed": 0, "gone": 0, "skipped": None}


def test_send_payloads_unconfigured(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    assert notify.send_payloads([{"title": "1"}], "mailto:a@b.c")["skipped"] == "VAPID_PRIVATE_KEY not set"


def test_pipeline_publishes_public_key(config, tmp_path, monkeypatch):
    from jobradar import pipeline
    from jobradar.offline import OfflineSession, enable_fake_keys
    enable_fake_keys()
    private, public = push.generate_vapid_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    out = tmp_path / "jobs.json"
    pipeline.run(config, session=OfflineSession(), output=out, ignore_throttle=True)
    assert json.loads(out.read_text())["push"] == {"public_key": public}
    # A later run without the secret (e.g. local) keeps the published key
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    pipeline.run(config, session=OfflineSession(), output=out, ignore_throttle=True)
    assert json.loads(out.read_text())["push"] == {"public_key": public}
