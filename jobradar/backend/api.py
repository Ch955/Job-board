"""Job Radar API: the only place that holds secrets (Gmail tokens, OAuth client
secret, encryption key). Every /api route needs a valid Firebase ID token and
acts only on that token's user.

Run locally:  uvicorn jobradar.backend.api:app --reload   (STORE=memory for a dry run)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from .. import gmail, push
from ..config import load_config
from ..http import make_session
from . import drafts, gmail_link, resume_intake, tasks
from .idtoken import InvalidToken, Verifier
from .profile import ProfileError, clean, is_complete
from .store import Conflict, FirestoreStore, MemoryStore, Store

log = logging.getLogger("jobradar.api")


@dataclass
class Settings:
    project_id: str = ""
    app_url: str = ""                 # where the web app lives, e.g. https://jobradar.pages.dev
    api_url: str = ""                 # this API's public https URL
    allowed_origins: list[str] = field(default_factory=list)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    cron_secret: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""
    android_scheme: str = "app.jobradar"   # deep link back into the Android app
    store: str = "firestore"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        origins = [o.strip().rstrip("/") for o in (e("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
        return cls(project_id=e("FIREBASE_PROJECT_ID", ""), app_url=(e("APP_URL") or "").rstrip("/"),
                   api_url=(e("API_URL") or "").rstrip("/"), allowed_origins=origins,
                   gmail_client_id=e("GMAIL_WEB_CLIENT_ID", ""), gmail_client_secret=e("GMAIL_WEB_CLIENT_SECRET", ""),
                   cron_secret=e("CRON_SECRET", ""), vapid_private_key=e("VAPID_PRIVATE_KEY", ""),
                   vapid_subject=e("VAPID_SUBJECT", "mailto:admin@example.com"),
                   android_scheme=e("ANDROID_SCHEME", "app.jobradar"), store=e("STORE", "firestore"))

    def oauth(self) -> gmail_link.OAuthConfig | None:
        if not (self.gmail_client_id and self.gmail_client_secret and self.api_url):
            return None
        return gmail_link.OAuthConfig(self.gmail_client_id, self.gmail_client_secret, f"{self.api_url}/api/gmail/callback")


class RateLimit:
    """Per-user cooldowns for expensive actions (single instance; enough for a free-tier deploy)."""

    def __init__(self):
        self.last: dict[tuple[str, str], float] = {}

    def check(self, uid: str, action: str, seconds: int) -> None:
        now = time.monotonic()
        key = (uid, action)
        if now - self.last.get(key, -1e9) < seconds:
            raise HTTPException(429, f"Please wait a little before trying {action} again.")
        self.last[key] = now


class PoolSearch:
    """One background job search at a time, started when someone needs fresh jobs.

    The scheduled run (GitHub Actions) refreshes the pool every 3 hours; this covers the
    gaps: a brand-new profile, new target roles, or a refresh after an hour. When it
    finishes, every user with a complete profile is re-scored.
    """

    MIN_GAP = 15 * 60  # seconds between searches, unless the pool is empty or misses someone's roles

    def __init__(self, store: Store, base: dict, fetcher=None, session_factory=make_session, background=True):
        self.store, self.base, self.fetcher = store, base, fetcher
        self.session_factory, self.background = session_factory, background
        self.lock = threading.Lock()
        self.running_since: float | None = None
        self.last_started = 0.0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self.running_since is not None

    def maybe_start(self, uid: str, reason: str | None) -> bool:
        """Start a search for `reason` (from tasks.pool_status). True if one is running now."""
        if not reason:
            return self.running
        with self.lock:
            if self.running:
                return True
            if reason == "stale" and time.monotonic() - self.last_started < self.MIN_GAP:
                return False
            self.running_since = time.time()
            self.last_started = time.monotonic()
        if self.background:
            threading.Thread(target=self._run, args=(uid,), daemon=True, name="pool-search").start()
        else:
            self._run(uid)
        return True

    def _run(self, uid: str) -> None:
        started = time.monotonic()
        try:
            kw = {"fetcher": self.fetcher} if self.fetcher else {}
            r = tasks.refresh_pool(self.store, self.base, self.session_factory(), first=uid, **kw)
            pool = tasks.load_pool(self.store)
            for other, p in tasks.profiles(self.store).items():
                try:
                    tasks.match_user(self.store, self.base, other, pool, profile=p)
                except Exception as e:  # noqa: BLE001 - one bad profile mustn't stop the others
                    log.warning("matching after search failed for %s: %s", _uid_tag(other), type(e).__name__)
            self.last_error = None
            log.info("pool search done: %s jobs in %ds", r.get("jobs"), time.monotonic() - started)
        except Exception as e:  # noqa: BLE001
            self.last_error = type(e).__name__
            log.warning("pool search failed: %s", type(e).__name__)
        finally:
            self.running_since = None

    def status(self) -> dict:
        meta = self.store.get("pool", "meta") or {}
        return {"searching": self.running, "search_started": self.running_since,
                "pool_updated": meta.get("updated"), "pool_count": meta.get("count", 0), "search_error": self.last_error}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid_tag(uid: str) -> str:  # for logs: never the uid itself, never tokens or emails
    return hashlib.sha256(uid.encode()).hexdigest()[:10]


def create_app(settings: Settings | None = None, store: Store | None = None, session=None,
               verifier: Verifier | None = None, base_config: dict | None = None,
               search: PoolSearch | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    session = session or make_session()
    if store is None:
        if settings.store == "memory":
            store = MemoryStore()
        else:
            sa = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]) if os.environ.get("FIREBASE_SERVICE_ACCOUNT") else None
            store = FirestoreStore(settings.project_id, session, sa)
    verifier = verifier or Verifier(settings.project_id, session)
    base = base_config if base_config is not None else load_config()
    limits = RateLimit()
    search = search or PoolSearch(store, base)
    app = FastAPI(title="Job Radar API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type",
                                                                                     "X-Filename"], max_age=3600)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        # Path only: query strings can hold OAuth codes and states
        log.info("%s %s %s %dms", request.method, request.url.path, response.status_code, (time.monotonic() - started) * 1000)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def user(authorization: str = Header(default="")) -> dict:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Sign in first.")
        try:
            return verifier.verify(authorization[7:].strip())
        except InvalidToken:
            raise HTTPException(401, "Your sign-in expired. Sign in again.") from None

    def verified(u: dict = Depends(user)) -> dict:
        if not u.get("email_verified"):
            raise HTTPException(403, "Verify your email address first (check your inbox).")
        return u

    def oauth_cfg() -> gmail_link.OAuthConfig:
        cfg = settings.oauth()
        if not cfg:
            raise HTTPException(503, "Gmail connection isn't set up on this server yet.")
        return cfg

    # ---------------------------------------------------------------- basics
    @app.get("/healthz")
    def healthz():
        meta = store.get("pool", "meta") or {}
        # Public, so only counts: which job sites answered in the last search
        sources = {k: {"status": v.get("status"), "jobs": v.get("count", 0)} for k, v in (meta.get("sources") or {}).items()
                   if v.get("status") not in ("disabled",)}
        return {"ok": True, "gmail": bool(settings.oauth()), "push": bool(settings.vapid_private_key),
                "jobs": {"in_list": meta.get("count", 0), "by_site": meta.get("by_site", {}),
                         "updated": meta.get("updated"), "searching": search.running,
                         "last_search": sources}}

    @app.get("/api/me")
    def me(u: dict = Depends(user)):
        uid = u["uid"]
        prof = store.get("profiles", uid) or {}
        m = store.get("matches", uid) or {}
        g = store.get("gmail", uid) or {}
        return {"uid": uid, "email": u.get("email"), "email_verified": u.get("email_verified", False),
                "profile": prof, "missing": is_complete(prof) if prof else ["resume or profile"],
                "resume": store.get("resumes", uid),
                "gmail": {k: g.get(k) for k in ("connected", "email", "connected_at", "last_success", "last_attempt",
                                                "last_error", "last_report")},
                "matches": {"count": m.get("count", 0), "updated": m.get("updated")},
                "features": {"gmail": bool(settings.oauth()), "push": bool(settings.vapid_private_key)}}

    @app.put("/api/profile")
    def put_profile(body: dict, u: dict = Depends(verified)):
        try:
            p = clean(body)
        except ProfileError as e:
            raise HTTPException(422, str(e)) from None
        old = store.get("profiles", u["uid"]) or {}
        p["updated"] = _now().isoformat(timespec="seconds")
        store.set("profiles", u["uid"], p)
        count, searching = None, False
        if not is_complete(p):
            try:
                count = tasks.match_user(store, base, u["uid"], profile=p).get("count")
            except Exception as e:  # noqa: BLE001 - the profile is saved; matches refresh next run
                log.warning("match after profile save failed: %s", type(e).__name__)
            searching = search.maybe_start(u["uid"], tasks.pool_status(store, p))
        return {"profile": p, "missing": is_complete(p), "threshold_changed": old.get("threshold") != p["threshold"],
                "count": count, "searching": searching}

    # ---------------------------------------------------------------- resume
    @app.post("/api/resume")
    async def upload_resume(request: Request, u: dict = Depends(verified), x_filename: str = Header(default="resume.pdf")):
        limits.check(u["uid"], "uploading", 10)
        if int(request.headers.get("content-length") or 0) > resume_intake.MAX_BYTES + 1024:
            raise HTTPException(413, "The file is over 2 MB.")
        data = await request.body()
        try:
            out = resume_intake.save(store, u["uid"], urllib.parse.unquote(x_filename), data)
        except resume_intake.ResumeError as e:
            raise HTTPException(422, str(e)) from None
        return out

    @app.get("/api/resume")
    def get_resume(u: dict = Depends(user)):
        return {"file": store.get("resumes", u["uid"])}

    @app.delete("/api/resume")
    def delete_resume(u: dict = Depends(user)):
        resume_intake.delete(store, u["uid"])
        return {"deleted": True}

    # --------------------------------------------------------------- matches
    @app.post("/api/matches/refresh")
    def refresh_matches(u: dict = Depends(verified)):
        limits.check(u["uid"], "refreshing", 10)
        prof = store.get("profiles", u["uid"])
        r = tasks.match_user(store, base, u["uid"], profile=prof)
        if r.get("skipped"):
            raise HTTPException(409, "Finish your profile first (skills and target role).")
        searching = search.maybe_start(u["uid"], tasks.pool_status(store, prof))
        return {"count": r["count"], **search.status(), "searching": searching}

    @app.get("/api/matches")
    def get_matches(u: dict = Depends(user)):
        m = store.get("matches", u["uid"]) or {}
        return {"jobs": m.get("jobs", []), "updated": m.get("updated"), "threshold": m.get("threshold"), **search.status()}

    # ----------------------------------------------------------------- gmail
    @app.post("/api/gmail/connect")
    def gmail_connect(body: dict | None = None, u: dict = Depends(verified)):
        cfg = oauth_cfg()
        limits.check(u["uid"], "connecting Gmail", 5)
        ret = (body or {}).get("return_to", "web")
        return {"url": gmail_link.start(store, cfg, u["uid"], login_hint=u.get("email", ""), return_to=ret)}

    @app.get("/api/gmail/callback")
    def gmail_callback(state: str = Query(default=""), code: str = Query(default=""), error: str = Query(default="")):
        # Google redirects the browser here; the state (not a header) identifies the user
        cfg = oauth_cfg()
        ret = ((store.get("oauth_states", state) or {}).get("return_to") if state else None) or "web"
        if error or not code:
            if state:
                store.delete("oauth_states", state)
            return _back(settings, ret, "cancelled" if error == "access_denied" else "error", "Gmail wasn't connected.")
        try:
            _, ret = gmail_link.finish(store, cfg, session, state, code)
        except gmail_link.LinkError as e:
            return _back(settings, ret, "error", str(e))
        except Exception as e:  # noqa: BLE001
            log.warning("gmail callback failed: %s", type(e).__name__)
            return _back(settings, ret, "error", "Something went wrong connecting Gmail. Try again.")
        return _back(settings, ret, "connected", "")

    @app.post("/api/gmail/disconnect")
    def gmail_disconnect(u: dict = Depends(user)):
        gmail_link.disconnect(store, session, u["uid"])
        return {"connected": False}

    @app.post("/api/gmail/sync")
    def gmail_sync(u: dict = Depends(verified)):
        cfg = oauth_cfg()
        limits.check(u["uid"], "syncing Gmail", 60)
        if not store.get("secrets", u["uid"]):
            raise HTTPException(409, "Connect Gmail first.")
        r = tasks.gmail_sync_user(store, cfg, session, u["uid"])
        g = store.get("gmail", u["uid"]) or {}
        if "error" in r:
            raise HTTPException(502, g.get("last_error") or "Gmail sync failed.")
        return {"report": r, "last_success": g.get("last_success")}

    # ---------------------------------------------------------------- drafts
    @app.post("/api/drafts")
    def create_draft(body: dict, u: dict = Depends(verified)):
        cfg = oauth_cfg()
        uid = u["uid"]
        kind = body.get("type", "")
        if kind not in drafts.TYPES:
            raise HTTPException(422, "Unknown draft type.")
        prof = store.get("profiles", uid) or {}
        try:
            client = gmail_link.access_for(store, cfg, session, uid)
        except gmail.GmailAuthError:
            raise HTTPException(409, "Connect Gmail first.") from None
        limits.check(uid, "creating a draft", 3)
        job = None
        if body.get("job_id"):
            jobs = (store.get("matches", uid) or {}).get("jobs") or []
            job = next((j for j in jobs if j["id"] == body["job_id"]), None)
            if job is None:
                state, _, _ = tasks.read_state(store, uid)
                job = (state.get("saved") or {}).get(body["job_id"])
            if job is None:
                raise HTTPException(404, "That job isn't in your list.")
        attachment = resume_intake.load(store, uid) if kind in drafts.ATTACH else None
        for _ in range(3):
            state, version, doc = tasks.read_state(store, uid)
            try:
                rec = drafts.create(client, state, prof, kind, job=job, to=(body.get("to") or "").strip(),
                                    reply_to_message=body.get("message_id"), resume=attachment)
            except drafts.DraftError as e:
                raise HTTPException(422, str(e)) from None
            except gmail.GmailAuthError:
                raise HTTPException(409, "Gmail needs to be connected again.") from None
            if rec.get("duplicate"):
                return {"draft": rec}
            try:
                tasks.write_state(store, uid, state, version, doc, _now())
                return {"draft": rec}
            except Conflict:
                # The draft exists in Gmail; record it on the fresh state without creating another
                state, version, doc = tasks.read_state(store, uid)
                state.setdefault("drafts", {})[drafts.key_for(kind, (job or {}).get("id"), rec.get("thread_id"))] = rec
                try:
                    tasks.write_state(store, uid, state, version, doc, _now())
                except Conflict:
                    pass
                return {"draft": rec}
        raise HTTPException(409, "Try again.")

    # ------------------------------------------------------------------ push
    @app.get("/api/push/key")
    def push_key():
        if not settings.vapid_private_key:
            raise HTTPException(503, "Notifications aren't set up on this server yet.")
        return {"publicKey": push.public_key_for(settings.vapid_private_key)}

    @app.post("/api/push/subscribe")
    def push_subscribe(body: dict, u: dict = Depends(user)):
        sub = body.get("subscription") or {}
        if not (isinstance(sub.get("endpoint"), str) and sub["endpoint"].startswith("https://")
                and (sub.get("keys") or {}).get("p256dh") and sub["keys"].get("auth")):
            raise HTTPException(422, "Not a push subscription.")
        subs = [s for s in (store.get("push", u["uid"]) or {}).get("subscriptions", []) if s["endpoint"] != sub["endpoint"]]
        subs = (subs + [{"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}])[-5:]
        store.set("push", u["uid"], {"subscriptions": subs})
        return {"devices": len(subs)}

    @app.delete("/api/push/subscribe")
    def push_unsubscribe(u: dict = Depends(user)):
        store.delete("push", u["uid"])
        return {"devices": 0}

    # --------------------------------------------------------------- account
    @app.delete("/api/account")
    def delete_account(u: dict = Depends(user)):
        delete_auth = getattr(store, "delete_auth_user", None)
        tasks.delete_user(store, session, u["uid"], delete_auth)
        log.info("account deleted %s", _uid_tag(u["uid"]))
        return {"deleted": True}

    # -------------------------------------------------------------- schedule
    @app.post("/internal/run")
    def scheduled_run(x_cron_secret: str = Header(default=""), refresh: bool = True):
        if not settings.cron_secret or not hmac.compare_digest(x_cron_secret, settings.cron_secret):
            raise HTTPException(403, "Forbidden")
        r = tasks.run_all(store, base, session, oauth=settings.oauth(), vapid_key=settings.vapid_private_key,
                          vapid_subject=settings.vapid_subject, refresh=refresh)
        return JSONResponse(r)

    return app


def _back(settings: Settings, return_to: str, status: str, message: str) -> RedirectResponse:
    q = urllib.parse.urlencode({"gmail": status, **({"message": message[:200]} if message else {})})
    if return_to == "android":
        return RedirectResponse(f"{settings.android_scheme}://gmail?{q}", status_code=302)
    return RedirectResponse(f"{settings.app_url or ''}/#{q}", status_code=302)


def _lazy_app():
    if os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("STORE") == "memory":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        return create_app()
    return None


app = _lazy_app()
