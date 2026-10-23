"""Connect / disconnect each user's own Gmail.

Flow: the signed-in user taps "Connect Gmail" → the API creates a one-time state
(bound to their uid, 10 minutes, with a PKCE verifier kept server-side) and sends
them to Google's consent screen → Google redirects to /api/gmail/callback → the
code is exchanged here with the client secret → the refresh token is encrypted
(bound to the uid) into secrets/{uid}; only non-secret status goes to gmail/{uid}.
Signing in to Job Radar with Google never grants Gmail access by itself.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import gmail
from . import secretbox
from .store import Store

STATE_TTL = 600
REQUIRED_SCOPES = set(gmail.WEB_SCOPES)


@dataclass
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str  # https://<api>/api/gmail/callback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start(store: Store, cfg: OAuthConfig, uid: str, login_hint: str = "", return_to: str = "web") -> str:
    state = secrets.token_urlsafe(32)
    verifier, challenge = gmail.pkce_pair()
    store.set("oauth_states", state, {"uid": uid, "verifier": verifier, "created": time.time(),
                                      "return_to": return_to if return_to in ("web", "android") else "web"})
    return gmail.build_auth_url(cfg.client_id, cfg.redirect_uri, state, challenge, login_hint or None)


class LinkError(Exception):
    pass


def finish(store: Store, cfg: OAuthConfig, session, state: str, code: str) -> tuple[str, str]:
    """Complete a connection. Returns (uid, return_to). Raises LinkError with a user-facing message."""
    pending = store.get("oauth_states", state) if state else None
    if pending:
        store.delete("oauth_states", state)  # one use only
    if not pending:
        raise LinkError("That Gmail link has expired or was already used. Tap Connect Gmail again.")
    if time.time() - float(pending.get("created", 0)) > STATE_TTL:
        raise LinkError("That Gmail link expired. Tap Connect Gmail again.")
    uid = pending["uid"]
    try:
        tokens = gmail.exchange_code(session, cfg.client_id, cfg.client_secret, code, cfg.redirect_uri, pending["verifier"])
    except gmail.GmailAuthError as e:
        raise LinkError(str(e)) from e
    granted = set((tokens.get("scope") or "").split())
    if not REQUIRED_SCOPES <= granted:
        missing = ", ".join(s.rsplit("/", 1)[1] for s in REQUIRED_SCOPES - granted)
        gmail.revoke(session, tokens.get("refresh_token") or tokens.get("access_token", ""))
        raise LinkError(f"Job Radar needs both Gmail permissions to work; this was missing: {missing}. Try again and tick both boxes.")
    if not tokens.get("refresh_token"):
        raise LinkError("Google didn't return long-term access. Remove Job Radar at myaccount.google.com/permissions and connect again.")
    address = gmail.GmailClient(session, tokens["access_token"]).profile().get("emailAddress", "")
    store.set("secrets", uid, {"gmail_refresh": secretbox.seal(tokens["refresh_token"].encode(), uid=uid, purpose="gmail"),
                               "scopes": sorted(granted), "email": address, "connected_at": _now()})
    store.update("gmail", uid, {"connected": True, "email": address, "connected_at": _now(), "last_error": None})
    return uid, pending.get("return_to", "web")


def disconnect(store: Store, session, uid: str) -> None:
    secret = store.get("secrets", uid)
    if secret and secret.get("gmail_refresh"):
        try:
            gmail.revoke(session, secretbox.open_(secret["gmail_refresh"], uid=uid, purpose="gmail").decode())
        except secretbox.SecretBoxError:
            pass
    store.delete("secrets", uid)
    store.update("gmail", uid, {"connected": False, "email": None, "disconnected_at": _now(), "last_error": None})


def access_for(store: Store, cfg: OAuthConfig, session, uid: str) -> gmail.GmailClient:
    """A Gmail client for this user only. Revoked access disconnects them with a clear message."""
    secret = store.get("secrets", uid)
    if not secret or not secret.get("gmail_refresh"):
        raise gmail.GmailAuthError("Gmail isn't connected", revoked=True)
    refresh = secretbox.open_(secret["gmail_refresh"], uid=uid, purpose="gmail").decode()
    try:
        token = gmail.access_token(session, cfg.client_id, cfg.client_secret, refresh)
    except gmail.GmailAuthError as e:
        if e.revoked:
            store.delete("secrets", uid)
            store.update("gmail", uid, {"connected": False, "email": None,
                                        "last_error": "Gmail access was removed or expired. Connect Gmail again."})
        raise
    return gmail.GmailClient(session, token)
