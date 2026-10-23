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
import urllib.parse
import webbrowser
from email.message import EmailMessage
from email.utils import make_msgid

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# compose: create drafts. readonly: read job-alert emails and recruiter replies (next step).
SCOPES = "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.readonly"


class GmailAuthError(Exception):
    pass


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
        raise GmailAuthError(f"{err}{hint}")
    return r.json()["access_token"]


def build_message(to: str, subject: str, body: str, sender: str | None = None,
                  attachment: tuple[str, bytes] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="jobradar.local")
    msg.set_content(body)
    if attachment:
        name, data = attachment
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return msg


def create_draft(session, token: str, *, to: str, subject: str, body: str, sender: str | None = None,
                 attachment: tuple[str, bytes] | None = None) -> dict:
    raw = base64.urlsafe_b64encode(build_message(to, subject, body, sender, attachment).as_bytes()).decode()
    r = session.post(f"{API}/drafts", headers={"Authorization": f"Bearer {token}"},
                     json={"message": {"raw": raw}})
    r.raise_for_status()
    d = r.json()
    return {"id": d["id"], "message_id": (d.get("message") or {}).get("id", "")}


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
