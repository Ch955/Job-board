"""Minimal Gmail API client over `requests`: OAuth refresh, create draft, and the
one-time local sign-in that gives you a refresh token. No Google SDK needed.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from email.message import EmailMessage
from email.utils import make_msgid

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# compose: create drafts. readonly: read job-alert emails and recruiter replies (next step).
SCOPES = "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.readonly"


DOCX_SUBTYPE = "vnd.openxmlformats-officedocument.wordprocessingml.document"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# The minimum for Job Radar's Gmail features, requested only when a user taps "Connect Gmail"
# (separately from signing in): readonly to spot job emails, compose to create drafts (never send).
WEB_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose")


class GmailAuthError(Exception):
    """Access token can't be obtained: revoked, expired or never granted. `revoked` = reconnect needed."""

    def __init__(self, message: str, revoked: bool = False):
        super().__init__(message)
        self.revoked = revoked


class GmailApiError(Exception):
    def __init__(self, status: int, message: str, reason: str = "", user_message: str = ""):
        super().__init__(f"Gmail API {status}{' ' + reason if reason else ''}: {message}")
        self.status, self.reason = status, reason
        self.user_message = user_message or f"Gmail returned an error ({status}{', ' + reason if reason else ''}). Will retry."


def google_error(r) -> tuple[str, str]:
    """(reason, message) from a Google API error body. Google's text, never request data."""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return "", (r.text or "")[:200]
    if isinstance(err, str):
        return err, ""
    reasons = [d.get("reason", "") for d in err.get("details") or [] if isinstance(d, dict)]
    reasons += [e.get("reason", "") for e in err.get("errors") or [] if isinstance(e, dict)]
    reason = next((x for x in reasons if x), err.get("status", ""))
    return reason, str(err.get("message", ""))[:200]


def access_token(session, client_id: str, client_secret: str, refresh_token: str) -> str:
    r = session.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        try:
            err = r.json().get("error", r.status_code)
        except ValueError:
            err = r.status_code
        hint = " (refresh token expired or revoked: run `python -m jobradar gmail-auth` again)" if err == "invalid_grant" else ""
        raise GmailAuthError(f"{err}{hint}", revoked=err == "invalid_grant")
    return r.json()["access_token"]


def build_message(to: str, subject: str, body: str, sender: str | None = None,
                  attachment: tuple[str, bytes] | None = None, in_reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    if to:  # a draft without a recipient is fine: you add it in Gmail
        msg["To"] = to
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="jobradar.local")
    if in_reply_to:  # keeps a reply in the recruiter's thread in their mail client too
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    if attachment:
        name, data = attachment
        subtype = DOCX_SUBTYPE if name.lower().endswith(".docx") else "pdf"
        msg.add_attachment(data, maintype="application", subtype=subtype, filename=name)
    return msg


def create_draft(session, token: str, *, to: str, subject: str, body: str, sender: str | None = None,
                 attachment: tuple[str, bytes] | None = None, thread_id: str | None = None,
                 in_reply_to: str | None = None) -> dict:
    """Create a Gmail draft (never sends). With thread_id it lands in that conversation."""
    raw = base64.urlsafe_b64encode(build_message(to, subject, body, sender, attachment, in_reply_to).as_bytes()).decode()
    message = {"raw": raw}
    if thread_id:
        message["threadId"] = thread_id
    r = session.post(f"{API}/drafts", headers={"Authorization": f"Bearer {token}"}, json={"message": message})
    if r.status_code == 401:
        raise GmailAuthError("Gmail access expired", revoked=False)
    if r.status_code == 403:
        raise GmailAuthError("Gmail didn't allow creating drafts (permission missing)", revoked=True)
    r.raise_for_status()
    d = r.json()
    msg = d.get("message") or {}
    return {"id": d["id"], "message_id": msg.get("id", ""), "thread_id": msg.get("threadId", thread_id or "")}


# ------------------------------------------------------- per-user web consent
def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id: str, redirect_uri: str, state: str, code_challenge: str,
                   login_hint: str | None = None, scopes=WEB_SCOPES) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
         "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "false",
         "code_challenge": code_challenge, "code_challenge_method": "S256"}
    if login_hint:
        q["login_hint"] = login_hint
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def exchange_code(session, client_id: str, client_secret: str, code: str, redirect_uri: str, verifier: str) -> dict:
    """Server-side code exchange. Returns Google's token response (with refresh_token)."""
    r = session.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret, "code": code,
                                      "code_verifier": verifier, "grant_type": "authorization_code",
                                      "redirect_uri": redirect_uri})
    if r.status_code != 200:
        try:
            err = r.json().get("error_description") or r.json().get("error")
        except ValueError:
            err = r.status_code
        raise GmailAuthError(f"Google refused the sign-in: {err}")
    return r.json()


def revoke(session, token: str) -> bool:
    """Revoke a refresh (or access) token. True if Google confirmed or it was already invalid."""
    try:
        r = session.post(REVOKE_URL, data={"token": token}, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return r.status_code in (200, 400)
    except Exception:  # noqa: BLE001 - best effort; the stored copy is deleted regardless
        return False


class GmailClient:
    """Thin Gmail REST client bound to one user's access token."""

    def __init__(self, session, token: str, sleep=time.sleep):
        self.session, self.token, self.sleep = session, token, sleep
        self._last = 0.0

    RATE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED")
    MIN_GAP = 0.1      # seconds between requests: ~10/s, well under Gmail's per-user quota
    RETRIES = 4        # on "slow down": wait 2, 4, 8, 16 s (or what Google asks), then give up

    def _get(self, path: str, params=None) -> dict:
        for attempt in range(self.RETRIES + 1):
            gap = self.MIN_GAP - (time.monotonic() - self._last)
            if gap > 0:
                self.sleep(gap)
            self._last = time.monotonic()
            r = self.session.get(f"{API}{path}", headers={"Authorization": f"Bearer {self.token}"}, params=params)
            limited = r.status_code == 429 or (r.status_code == 403 and google_error(r)[0] in self.RATE_REASONS)
            if not limited or attempt == self.RETRIES:
                break
            try:
                wait = float(r.headers.get("Retry-After") or 0)
            except (TypeError, ValueError, AttributeError):
                wait = 0
            self.sleep(min(max(wait, 2 ** (attempt + 1)), 60))
        if r.status_code == 401:
            raise GmailAuthError("Gmail access expired or was revoked", revoked=True)
        if r.status_code >= 400:
            reason, msg = google_error(r)
            if r.status_code == 403 and (reason in ("accessNotConfigured", "SERVICE_DISABLED")
                                         or "has not been used" in msg or "is disabled" in msg):
                raise GmailApiError(403, msg, reason or "SERVICE_DISABLED",
                                    "The Gmail API isn't enabled for this app's Google Cloud project. The app owner must enable "
                                    "it (Google Cloud → APIs & Services → Gmail API → Enable), then tap Sync now again.")
            if r.status_code == 429 or (r.status_code == 403 and reason in self.RATE_REASONS):
                raise GmailApiError(429, msg, reason or "429",
                                    f"Gmail is still limiting requests after several retries ({reason or 'rate limit'}: "
                                    f"{msg[:140] or 'no detail from Google'}). It will try again on the next run.")
            if r.status_code == 403:  # insufficientPermissions / ACCESS_TOKEN_SCOPE_INSUFFICIENT
                raise GmailAuthError(f"Gmail permission missing ({reason or 'forbidden'})", revoked=True)
            raise GmailApiError(r.status_code, msg, reason)
        return r.json()

    def profile(self) -> dict:
        return self._get("/profile")

    def list_message_ids(self, query: str, limit: int = 300) -> list[str]:
        ids, page = [], None
        while len(ids) < limit:
            params = {"q": query, "maxResults": min(100, limit - len(ids))}
            if page:
                params["pageToken"] = page
            j = self._get("/messages", params)
            ids += [m["id"] for m in j.get("messages", [])]
            page = j.get("nextPageToken")
            if not page:
                break
        return ids

    def message(self, mid: str, full: bool = False) -> dict:
        if full:
            return self._get(f"/messages/{mid}", {"format": "full"})
        return self._get(f"/messages/{mid}", {"format": "metadata", "metadataHeaders": ["Subject", "From", "Message-ID"]})

    def create_draft(self, **kw) -> dict:
        return create_draft(self.session, self.token, **kw)


def message_text(msg: dict, limit: int = 6000) -> str:
    """Plain text of a 'full' message (first text/plain part, else stripped HTML). Used, never stored."""
    import html as _html
    import re as _re

    def walk(part):
        yield part
        for p in part.get("parts") or []:
            yield from walk(p)

    plain, htmls = [], []
    for part in walk(msg.get("payload") or {}):
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        (plain if part.get("mimeType") == "text/plain" else htmls if part.get("mimeType") == "text/html" else []).append(text)
    text = "\n".join(plain) or _re.sub(r"<[^>]+>", " ", _html.unescape("\n".join(htmls)))
    return _re.sub(r"\s+", " ", text)[:limit]


# ----------------------------------------------------------------- sign-in
def read_client_file(path: str) -> tuple[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    c = data.get("installed") or data.get("web") or data
    return c["client_id"], c["client_secret"]


def interactive_auth(client_id: str, client_secret: str, session, open_browser: bool = True) -> str:
    """Local loopback OAuth flow (Google 'Desktop app' client). Returns a refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                result.update(code=q.get("code", [""])[0], error=q.get("error", [""])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab and go back to the terminal." if result.get("code") else \
                  "Sign-in didn't complete. Go back to the terminal."
            self.wfile.write(f"<p style='font:16px sans-serif'>{msg}</p>".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("\nOpen this link to sign in with your Gmail account (it should open by itself):\n\n" + url + "\n")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    while not result:
        server.handle_request()
    server.server_close()
    if not result.get("code"):
        raise GmailAuthError(result.get("error") or "no code returned")

    r = session.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
    })
    if r.status_code != 200:
        raise GmailAuthError(r.text[:300])
    token = r.json().get("refresh_token")
    if not token:
        raise GmailAuthError("Google didn't return a refresh token. Remove the app's access at "
                             "myaccount.google.com/permissions and try again.")
    return token


def env_or_file(path: str | None) -> tuple[str, str]:
    if path:
        return read_client_file(path)
    cid, sec = os.environ.get("GMAIL_CLIENT_ID", ""), os.environ.get("GMAIL_CLIENT_SECRET", "")
    if not (cid and sec):
        raise GmailAuthError("pass --client-file client_secret.json (downloaded from Google Cloud)")
    return cid, sec
