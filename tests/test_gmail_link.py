"""Per-user Gmail connection: one-time state, PKCE, scopes, encryption bound to the uid, revoke."""

import time
import urllib.parse

import pytest

from jobradar import gmail
from jobradar.backend import gmail_link, secretbox
from jobradar.backend.store import MemoryStore

CFG = gmail_link.OAuthConfig("cid", "csecret", "https://api.example/api/gmail/callback")
BOTH = " ".join(gmail.WEB_SCOPES)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", secretbox.new_key())


class Resp:
    def __init__(self, status, payload): self.status_code, self.p, self.text = status, payload, ""
    def json(self): return self.p


class FakeGoogle:
    def __init__(self, scope=BOTH, refresh="rt-alice", email="alice@gmail.com", refresh_ok=True):
        self.scope, self.refresh, self.email, self.refresh_ok = scope, refresh, email, refresh_ok
        self.exchanges, self.revoked, self.profile_tokens = [], [], []

    def post(self, url, data=None, headers=None, **kw):
        if url == gmail.REVOKE_URL:
            self.revoked.append(data["token"])
            return Resp(200, {})
        if data["grant_type"] == "authorization_code":
            self.exchanges.append(data)
            if data["code"] != "good-code":
                return Resp(400, {"error": "invalid_grant", "error_description": "Bad code"})
            body = {"access_token": "at-1", "scope": self.scope}
            if self.refresh:
                body["refresh_token"] = self.refresh
            return Resp(200, body)
        if data["grant_type"] == "refresh_token":
            return Resp(200, {"access_token": "at-2"}) if self.refresh_ok else Resp(400, {"error": "invalid_grant"})
        raise AssertionError(url)

    def get(self, url, headers=None, params=None):
        assert url.endswith("/profile")
        self.profile_tokens.append(headers["Authorization"])
        return Resp(200, {"emailAddress": self.email})


def state_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def test_start_builds_consent_url_with_pkce_and_minimum_scopes():
    store = MemoryStore()
    url = gmail_link.start(store, CFG, "alice", login_hint="alice@gmail.com")
    q = state_of(url)
    assert q["scope"] == [BOTH] and q["access_type"] == ["offline"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [CFG.redirect_uri] and q["login_hint"] == ["alice@gmail.com"]
    assert "client_secret" not in url and "csecret" not in url
    saved = store.get("oauth_states", q["state"][0])
    assert saved["uid"] == "alice" and saved["verifier"] and saved["verifier"] not in url


def test_finish_stores_encrypted_token_for_that_user_only():
    store, g = MemoryStore(), FakeGoogle()
    st = state_of(gmail_link.start(store, CFG, "alice"))["state"][0]
    uid, ret = gmail_link.finish(store, CFG, g, st, "good-code")
    assert (uid, ret) == ("alice", "web")
    assert g.exchanges[0]["code_verifier"] and g.exchanges[0]["client_secret"] == "csecret"  # server-side exchange
    secret = store.get("secrets", "alice")
    assert "rt-alice" not in repr(secret)                                   # encrypted at rest
    assert secretbox.open_(secret["gmail_refresh"], uid="alice", purpose="gmail") == b"rt-alice"
    with pytest.raises(secretbox.SecretBoxError):                         # can't be replayed as another user
        secretbox.open_(secret["gmail_refresh"], uid="bob", purpose="gmail")
    status = store.get("gmail", "alice")
    assert status["connected"] and status["email"] == "alice@gmail.com" and "gmail_refresh" not in status
    assert store.get("secrets", "bob") is None and store.get("gmail", "bob") is None


def test_state_is_single_use_and_expires(monkeypatch):
    store, g = MemoryStore(), FakeGoogle()
    st = state_of(gmail_link.start(store, CFG, "alice"))["state"][0]
    gmail_link.finish(store, CFG, g, st, "good-code")
    with pytest.raises(gmail_link.LinkError, match="already used"):
        gmail_link.finish(store, CFG, g, st, "good-code")
    with pytest.raises(gmail_link.LinkError):
        gmail_link.finish(store, CFG, g, "made-up", "good-code")
    st2 = state_of(gmail_link.start(store, CFG, "alice"))["state"][0]
    real = time.time
    monkeypatch.setattr(gmail_link.time, "time", lambda: real() + gmail_link.STATE_TTL + 5)
    with pytest.raises(gmail_link.LinkError, match="expired"):
        gmail_link.finish(store, CFG, g, st2, "good-code")
    assert len(g.exchanges) == 1  # expired / reused states never reach Google


def test_missing_permission_is_rejected_and_revoked():
    store, g = MemoryStore(), FakeGoogle(scope=gmail.WEB_SCOPES[0])
    st = state_of(gmail_link.start(store, CFG, "alice"))["state"][0]
    with pytest.raises(gmail_link.LinkError, match="gmail.compose"):
        gmail_link.finish(store, CFG, g, st, "good-code")
    assert g.revoked == ["rt-alice"] and store.get("secrets", "alice") is None


def test_no_refresh_token_or_bad_code():
    store = MemoryStore()
    st = state_of(gmail_link.start(store, CFG, "alice"))["state"][0]
    with pytest.raises(gmail_link.LinkError, match="long-term"):
        gmail_link.finish(store, CFG, FakeGoogle(refresh=None), st, "good-code")
    st = state_of(gmail_link.start(store, CFG, "alice"))["state"][0]
    with pytest.raises(gmail_link.LinkError, match="Bad code"):
        gmail_link.finish(store, CFG, FakeGoogle(), st, "bad-code")


def test_access_and_disconnect():
    store, g = MemoryStore(), FakeGoogle()
    gmail_link.finish(store, CFG, g, state_of(gmail_link.start(store, CFG, "alice"))["state"][0], "good-code")
    client = gmail_link.access_for(store, CFG, g, "alice")
    assert client.token == "at-2"
    with pytest.raises(gmail.GmailAuthError):
        gmail_link.access_for(store, CFG, g, "bob")                       # bob never connected
    gmail_link.disconnect(store, g, "alice")
    assert g.revoked == ["rt-alice"] and store.get("secrets", "alice") is None
    assert store.get("gmail", "alice")["connected"] is False


def test_revoked_access_disconnects_with_a_message():
    store, g = MemoryStore(), FakeGoogle()
    gmail_link.finish(store, CFG, g, state_of(gmail_link.start(store, CFG, "alice"))["state"][0], "good-code")
    g.refresh_ok = False
    with pytest.raises(gmail.GmailAuthError):
        gmail_link.access_for(store, CFG, g, "alice")
    assert store.get("secrets", "alice") is None
    assert "Connect Gmail again" in store.get("gmail", "alice")["last_error"]


RATE = {"error": {"code": 403, "message": "Quota exceeded for quota metric 'Queries' per minute per user.",
                  "errors": [{"reason": "rateLimitExceeded"}]}}


class Flaky:
    def __init__(self, fails):
        self.fails, self.calls = fails, 0
    def get(self, url, headers=None, params=None):
        self.calls += 1
        if self.calls <= self.fails:
            return Resp(403, RATE)
        return Resp(200, {"emailAddress": "a@gmail.com"})


def test_rate_limit_is_retried_with_backoff():
    waits = []
    s = Flaky(fails=2)
    assert gmail.GmailClient(s, "t", sleep=waits.append).profile()["emailAddress"] == "a@gmail.com"
    assert s.calls == 3 and [w for w in waits if w >= 1] == [2, 4]


def test_persistent_rate_limit_explains_with_googles_message():
    s = Flaky(fails=99)
    with pytest.raises(gmail.GmailApiError) as e:
        gmail.GmailClient(s, "t", sleep=lambda w: None).profile()
    assert e.value.status == 429 and "per minute per user" in e.value.user_message
    assert s.calls == gmail.GmailClient.RETRIES + 1
