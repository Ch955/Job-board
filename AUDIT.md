# Job Radar: audit before the multi-user work

Date: 2026-10-23. Baseline: `python -m pytest` → **131 passed**, 0 failed (verified, not assumed).

## Architecture as found
| Layer | What exists |
|---|---|
| Frontend | Static PWA in `docs/` (vanilla JS, no build step): `index.html`, `app.js`, `app.css`, `sw.js`, `manifest.webmanifest`, `cloud.js` (Firebase), `sync.js` (merge rules) |
| Hosting | GitHub Pages, deployed by `.github/workflows/job-radar.yml` |
| Auth | Firebase Authentication (email/password, Google) via the JS SDK from gstatic |
| Database | Firestore: one doc per user, `users/{uid}` = `{state: JSON string, email, updated}` |
| Background | GitHub Actions cron every 3 h: `python -m jobradar run --notify --drafts`, then `gmail-sync` |
| Job sources | `jobradar/sources/`: Greenhouse, Lever, Ashby, SmartRecruiters, JobSpy (LinkedIn/Naukri/Indeed scraping), Adzuna, Jooble, Remotive, Arbeitnow |
| Scoring | `jobradar/scoring.py`: title 25 + skills 35 + location 20 + experience 10 + recency 10 − penalties, all from `config.yaml` |
| Gmail | `gmail.py` (OAuth desktop loopback, drafts), `gmail_status.py` (metadata + snippet classification), `outreach.py` (application drafts) |
| Data file | `docs/jobs.json`, public, rebuilt each run |

## 1. Working features
- Fetching from 9 sources with per-source isolation, throttling, dedupe, India-only, 2–4 yr band, Hyderabad-only walk-ins.
- Rule-based scoring with a breakdown, matched keywords, resume-tuned weights, AWS/GCP stack penalty.
- PWA: sections, filters (multi-select), pending/applied colours, Back handling, CSV export, backup, dark mode, offline.
- Firebase email/password + Google sign-in, password reset, logout, live cross-device sync with tested merge rules.
- Web Push notifications (single subscriber list in a secret).
- Gmail drafts and Gmail status detection, **single user only**, driven by one refresh token in GitHub Secrets.

## 2. Incomplete
- Everything per-user: profile, resume, preferences and Gmail all come from `config.yaml` / secrets of one person.
- No resume upload in the app; `resume.py` only encrypts one PDF via the CLI.
- No "Connect Gmail" in the app; no disconnect, revoke or status.
- Match threshold fixed at ≥ 40 (`pipeline.min_score`), not per user; no "missing skills" explanation.
- Drafts: only the initial application type; no follow-up, reply, interview or thank-you drafts; no thread replies.
- Gmail review of uncertain matches: none (low-confidence events are applied or dropped).
- No account deletion. No Android packaging.

## 3. Bugs and security concerns
- **Wrong-job risk:** `gmail_status.match` accepts an email on company-name match alone when that company has one job. Required: confidence + review.
- **Classification on snippet only** with broad words ("application"): a digest or newsletter can still be misread.
- **Hard-coded personal defaults:** `outreach.fields()` and `app.js fillTemplate()` fall back to "ADF, Databricks, PySpark" for every user.
- **Single-tenant secrets:** Gmail refresh token, resume key and push subscriptions are GitHub Secrets for one person.
- **Public data:** `docs/jobs.json` is public and includes recruiter emails found in postings. Acceptable for public postings, but per-user data (matches, statuses, resume facts) must never be written there.
- Firestore rules cover only `users/{uid}`; no server-only collections exist yet.
- Earlier in this project a resume key and a VAPID key were pasted in chat. Rotate both before production.

## 4. Missing credentials / external integrations (cannot be created from this environment)
Firebase project config + service account · Google OAuth **Web** client for Gmail (+ consent screen, verification) ·
`APP_ENCRYPTION_KEY` · VAPID keys · a backend host account (Cloud Run needs a billing account) · Cloudflare account ·
Google Play Console account. Android SDK downloads (`dl.google.com`) are blocked here, so an AAB can't be built in this sandbox.

## 5. Required for multi-user
Per-user profile/preferences/resume/Gmail; a backend that holds secrets (Gmail refresh tokens, OAuth client secret)
and verifies Firebase ID tokens; server-only Firestore collections; a shared job pool scored per user; per-user
notifications and drafts; account deletion; rules tests.

## 6. Sequence followed
Backend core (token verification, encrypted secret storage, data store) → Gmail OAuth per user → Gmail events with
confidence + review → drafts → resume upload + extraction → per-user matching with explanations → security rules
(emulator-tested) → API + scheduler → frontend → deployment docs → Android (Capacitor) → Play Store docs.
