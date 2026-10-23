# Job Radar: multi-user production deployment

This turns the single-person Job Radar into an app anyone can sign up for: each user
has their own profile, resume, matches, Gmail connection, review list and drafts.
Nothing here costs money on the recommended path, but **some steps need accounts only
you can create**, and two options need a billing account (marked). Nothing has been
deployed yet: no URLs below are live until you do these steps.

## 1. Architecture

```
 Browser / Android app (docs/, mobile/)        static files on Cloudflare Pages (or GitHub Pages)
   │  Firebase Auth (email+password, Google)  ── sign-in only; never grants Gmail
   │  Firestore users/{uid}  (live sync, own doc only: firestore.rules)
   │  HTTPS + Firebase ID token
   ▼
 Job Radar API (jobradar/backend/api.py)       container on Render free (or Cloud Run)
   │  verifies the token on every request, holds every secret
   ├─ Firestore (service account): profiles, matches, gmail status, encrypted secrets,
   │  encrypted resumes, push subscriptions, OAuth states, job pool
   └─ Google OAuth (Gmail), Gmail API, Web Push

 Scheduler: GitHub Actions every 3 h → python -m jobradar.backend run-all
   refresh shared job pool → score it for each user → alert each user → sync each
   connected Gmail (each user isolated; a failure is recorded on that user only)
```

| Collection | Contents | Who can read |
|---|---|---|
| `users/{uid}` | statuses, Gmail events + review list, review answers, draft records | the user (read/write) |
| `profiles/{uid}` | confirmed profile, threshold, preferences | the user (read) |
| `matches/{uid}` | scored jobs + explanations | the user (read) |
| `gmail/{uid}` | connected?, address, last successful sync, last error | the user (read) |
| `secrets/{uid}` | Gmail refresh token, AES-256-GCM, bound to the uid | server only |
| `resumes/{uid}`, `resume_chunks/*` | resume file, encrypted, bound to the uid | server only |
| `push/{uid}`, `oauth_states/*`, `pool/*` | push subscriptions, pending Gmail links, shared jobs | server only |

The rules are in `firestore.rules` and are tested against the Firestore emulator
(`tests/rules/`, 5 tests, also run in `.github/workflows/tests.yml`).

## 2. Hosting choices (checked October 2026, re-check before relying on them)

| Part | Recommended (₹0) | Alternative |
|---|---|---|
| Web app | **Cloudflare Pages**, direct upload of `docs/` (free plan: 500 builds/month, 20,000 files, 25 MiB per file) | Keep GitHub Pages (already working) |
| API | **Render free web service** with `render.yaml` + `Dockerfile`. Spins down after 15 minutes idle; the first request after that takes about a minute. 750 free instance hours per month | **Google Cloud Run** (free tier: 2 million requests and 180,000 vCPU-seconds per month for request-based billing) — **needs a billing account with a card: ask before enabling** |
| Scheduler | **GitHub Actions** (already runs every 3 h; free for public repos) | Cloud Scheduler → `POST /internal/run` with `X-Cron-Secret` (billing account) |
| Database + sign-in | Firebase Spark (free) plan | — |

Do not keep the Render service awake with pings: Render says that goes against its free tier.
The scheduled work runs in GitHub Actions, not in the API, so sleeping is fine.

## 3. Environment variables

All names are in `deploy/env.example`. Put values only in the host's secret settings
(Render dashboard, GitHub → Settings → Secrets). Never in the repo or the web app.

| Name | Where | What |
|---|---|---|
| `FIREBASE_PROJECT_ID` | API, Actions | Firebase project id |
| `FIREBASE_SERVICE_ACCOUNT` | API, Actions | service account JSON (Firestore + Firebase Auth admin) |
| `APP_ENCRYPTION_KEY` | API, Actions (same value) | `python -m jobradar.backend new-key`. **Back it up privately**: losing it makes stored Gmail links and resumes unreadable (users would reconnect/re-upload) |
| `GMAIL_WEB_CLIENT_ID`, `GMAIL_WEB_CLIENT_SECRET` | API, Actions | Google OAuth **Web application** client for Gmail |
| `API_URL` | API, Actions | public https URL of the API, e.g. `https://jobradar-api.onrender.com` |
| `APP_URL` | API | public URL of the web app, e.g. `https://jobradar.pages.dev` |
| `ALLOWED_ORIGINS` | API | `https://jobradar.pages.dev,https://localhost` (the second is the Android app) |
| `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | API, Actions | web push keys (`python -m jobradar push-keys`) |
| `CRON_SECRET` | API | only if an external scheduler calls `/internal/run` |

Web app config (`docs/app-config.json`, public by design):
```json
{ "firebase": { "apiKey": "…", "authDomain": "…", "projectId": "…", "appId": "…" },
  "api_url": "https://jobradar-api.onrender.com" }
```
With `api_url` set the app runs in multi-user mode; without it, it keeps working as the
single-user app from `jobs.json`.

## 4. Step by step

1. **Rotate old keys.** A resume key and a VAPID key were pasted in chat earlier in this
   project. Generate new ones before going public.
2. **Firebase** (console.firebase.google.com): Authentication → enable Email/Password and
   Google. Authentication → Settings → Authorized domains: add your Pages domain.
   Firestore → Rules: paste `firestore.rules` → Publish. Project settings → Service accounts →
   Generate new private key → that JSON is `FIREBASE_SERVICE_ACCOUNT`.
3. **Gmail OAuth client** (console.cloud.google.com, same project): APIs & Services →
   enable **Gmail API**. OAuth consent screen: app name, support email, privacy policy URL
   (`https://<your pages domain>/privacy.html`), scopes `gmail.readonly` and `gmail.compose`.
   Credentials → Create OAuth client ID → **Web application** → Authorized redirect URI:
   `https://<API_URL host>/api/gmail/callback`. Copy client id and secret.
4. **API on Render**: New → Blueprint → this repo (uses `render.yaml`). Fill the secret
   values. After the first deploy, open `https://<service>.onrender.com/healthz`: it should
   say `{"ok": true, "gmail": true, "push": true}`.
5. **Web app on Cloudflare Pages**: set `api_url` in `docs/app-config.json`, then
   `npx wrangler pages deploy docs --project-name jobradar` (or connect the repo with no
   build command and `docs` as the output folder).
6. **Scheduler**: add the GitHub Secrets from section 3. The existing "Job Radar" workflow
   now runs `python -m jobradar.backend run-all` after its own steps; it prints
   "skipped" until the secrets exist.
7. **Check it**: sign up with a fresh email → verify it → upload a resume → fix the
   suggested fields → save → matches appear → Settings → Connect Gmail → Sync now.

## 5. Security notes

- Every `/api` route verifies the Firebase ID token (signature, audience, issuer, expiry)
  and only touches that user's documents. Uploads, Gmail and drafts also need a verified email.
- Gmail is connected separately from sign-in, with a one-time state (10 minutes) and PKCE;
  the code exchange happens on the server; refresh tokens are encrypted and bound to the uid.
  Disconnect revokes at Google and deletes the stored copy.
- Drafts are only ever created, never sent. Email bodies are read only when a subject is
  unclear and are never stored. Uncertain matches go to the user's review list.
- CORS is an allow-list; responses are `no-store`; logs carry method, path, status and time
  only (no query strings, tokens, emails or uids).
- Account deletion (Settings → Delete my account) revokes Gmail, deletes every per-user
  document and the Firebase sign-in.

## 6. Gmail access for the public: Google verification

`gmail.readonly` and `gmail.compose` are **restricted** scopes. Until Google verifies the app:
users see an "unverified app" warning and at most 100 test users can connect (add them
under OAuth consent screen → Test users). For a public launch Google requires app
verification **and** a yearly third-party security assessment (CASA). Vendors quote
roughly US$540–1,000 per year for the self-serve tier and several weeks; that is a paid
step and needs your approval. Everything else in Job Radar works without Gmail.

## 7. Known limitations

- Job sources: company boards (Greenhouse, Lever, Ashby, SmartRecruiters) and the keyed APIs
  (Adzuna, Jooble) are compliant. LinkedIn/Naukri/Indeed come from scraping (JobSpy), which
  those sites' terms don't allow; keep `jobspy` off (`config.yaml`) for a public service.
- Location scoring is India-focused (`config.yaml → location`).
- Rate limits in the API are per instance (fine for one free instance).
- Web push doesn't work inside the Android app's web view; users enable alerts in Chrome.
- Resume extraction is rule-based: scanned (image-only) PDFs can't be read, and suggestions
  can be wrong, which is why every field is confirmed by the user.
