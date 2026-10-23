# Job Radar

A personal job-alert system that costs ₹0 to run: a Python fetcher and scorer run by
GitHub Actions every 3 hours, a `docs/jobs.json` data file, your own mobile web app on GitHub Pages,
and phone notifications sent straight from that app.
No paid APIs, no paid hosting, no LLM calls.

> **Status:** done: fetchers + scoring, GitHub Actions, the app and phone alerts.
> Next: Gmail (recruiter replies, interview invites, rejections and offers).

**Your app:** <https://ch955.github.io/job-board/> (live after the one-time setup below)

## How it works

```
sources (one module each, isolated)        processing                         output
───────────────────────────────────        ──────────────────────────────     ─────────────────
Greenhouse / Lever / Ashby /         ─┐    title gate (is it a DE role?)
SmartRecruiters company boards        │    dedupe: company + title + city
LinkedIn, Naukri, Indeed (JobSpy)     ├──▶ score 0-100 (config.yaml rules) ──▶ docs/jobs.json
Adzuna, Jooble (free keys), Remotive  │    drop: >30 days, <40, outside India
Gmail job-alert emails (phase 5)     ─┘    merge with history (keep status)
```

Every source runs in its own `try` block. A dead API, a bad company slug or a missing key
logs a warning in the run report and the rest carry on.

## One-time setup on GitHub (about 10 minutes)

Do these once, in order. Everything is free because the repo is public.

### 1. Turn on GitHub Pages
Repo → **Settings** → **Pages** → under *Build and deployment*, set **Source** to **GitHub Actions**.

### 2. Add your secrets
Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add the ones you have:

| Secret | Where it comes from | Needed? |
|---|---|---|
| `VAPID_PRIVATE_KEY` | run `python -m jobradar push-keys` on your laptop and copy the private key | for phone alerts |
| `WEBPUSH_SUBSCRIPTIONS` | the app's ☰ menu, after you tap *Enable alerts* (step 4) | for phone alerts |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | `python -m jobradar gmail-auth` ([Gmail setup](#one-time-gmail-setup-about-15-minutes-free)) | for recruiter drafts |
| `RESUME_KEY` | `python -m jobradar encrypt-resume` | to attach your resume |
| `EMAIL_SIGNATURE` | your own text, e.g. your phone number | optional |
| `SYNC_PASSPHRASE` | the passphrase you sign in to the app with | for Gmail status updates |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | [Adzuna](#adzuna) | optional |
| `JOOBLE_API_KEY` | [Jooble](#jooble) | optional |

Secrets are encrypted by GitHub and never appear in the code, the logs or `jobs.json`.

### 3. Run it once
Repo → **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**. Then
**Job Radar** → **Run workflow** → mode **fetch** → **Run workflow**. After a few minutes it
commits `docs/jobs.json` and deploys the app. Open <https://ch955.github.io/job-board/>.

From now on it runs by itself every 3 hours.

### 4. Turn on phone alerts
1. On your phone, open the app and install it:
   - **Android (Chrome):** ⋮ menu → **Install app** (or *Add to Home screen*).
   - **iPhone (Safari):** Share button → **Add to Home Screen**. On iPhone, alerts *only* work from the installed app (iOS 16.4+).
2. Open Job Radar from your home screen → ☰ menu → **Enable alerts on this device** → allow notifications.
3. Tap **Copy code**, and save it on GitHub as the secret `WEBPUSH_SUBSCRIPTIONS`.
   For a second phone, put each device's code on its own line in the same secret.
4. **Actions** → **Job Radar** → **Run workflow** → mode **test-notification**. Your phone should buzz.

You'll now get a notification for every new job scoring 70+ (at most 5 per run, then one "+N more"),
and a daily summary at 9 AM IST. Tap a notification to open that job in the app, or tap **Apply** to
go straight to the posting.

If you ever reinstall the app or clear its data, repeat step 4. The old code stops working, and the
run log says so.

## Rerun it yourself

Repo → **Actions** → **Job Radar** (left sidebar) → **Run workflow** (right) → pick a mode → **Run workflow**:

| Mode | What it does |
|---|---|
| **fetch** | a normal run now, instead of waiting up to 3 hours |
| **fresh-fetch** | clears the job list and rebuilds it with the current rules (use after changing `config.yaml`). Your Applied / Interview / Later choices on the phone are kept, and recruiters already emailed aren't drafted again |
| summary | sends the daily summary notification now |
| test-notification | sends a test notification to your phone |

A run takes about 5 minutes. Then pull down / tap ⟳ in the app.

## Recruiter outreach

### LinkedIn (on every card in the app)
- **👤 Find recruiter** opens a LinkedIn people search for recruiters at that company.
- **📋 Copy note** copies a connection note (under LinkedIn's 200-character limit) written from the job and your
  profile. On LinkedIn, tap **Connect** → **Add a note** → paste → **Send**.

Nothing is sent on LinkedIn for you. LinkedIn bans accounts that automate messages, and this is your real profile.
Edit the note's wording under `outreach.linkedin_note` in `config.yaml`.

### Gmail drafts (automatic, never sent)
When a strong match (score ≥ 70) lists a recruiter's email in the posting, each run creates a **draft** in your
Gmail, addressed to them, with your resume attached. You open it, adjust if you like, and press Send. Rules:
- at most 10 drafts per run, best jobs first;
- never two drafts for the same job, or to the same address within 30 days;
- the wording lives under `outreach.email` in `config.yaml`.

The app's ☰ › **Recruiter emails** section lists every draft with **Open draft** and **Mark sent**. Jobs that list an
email but have no draft get a **✉️ Email** button that opens your mail app with the message already written.

#### One-time Gmail setup (about 15 minutes, free)
1. **Google Cloud project.** Go to <https://console.cloud.google.com>, sign in as chakri.evn@gmail.com, and create a
   project named `Job Radar`. No billing account is needed.
2. **Enable the Gmail API.** **APIs & Services** → **Library** → search "Gmail API" → **Enable**.
3. **Consent screen.** **Google Auth Platform** (or *OAuth consent screen*) → **Get started**. App name `Job Radar`,
   support email chakri.evn@gmail.com, audience **External**, then finish.
4. **Publish it.** **Audience** → **Publish app** → confirm. This matters: while an app is in *Testing*, Google
   expires its sign-in after 7 days. It stays private to you either way.
5. **Desktop client.** **Clients** → **Create client** → application type **Desktop app** → **Create** →
   **Download JSON**. Save the `client_secret_….json` file in the project folder. It's git-ignored, so it won't be committed.
6. **Sign in once,** on your laptop in the project folder:
   ```bash
   python -m jobradar gmail-auth --client-file client_secret_XXXX.json
   ```
   A browser opens. Pick chakri.evn@gmail.com. Google warns *"Google hasn't verified this app"*. That's expected
   for a personal app: click **Advanced** → **Go to Job Radar (unsafe)** → allow. It's your own app, used only by you.
   The terminal prints three values. Add each as a GitHub Secret: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`.
7. **Resume.** Encrypt it so the public repo can carry it safely:
   ```bash
   python -m jobradar encrypt-resume "C:\path\to\Chakri_Azure_Data_Engineer_3yrs.pdf"
   git add assets/resume.pdf.enc && git commit -m "Add encrypted resume" && git push
   ```
   Add the printed key as the GitHub Secret `RESUME_KEY`. Without the key the file is unreadable.
8. **Optional:** add a GitHub Secret `EMAIL_SIGNATURE` (e.g. `Phone: +91-XXXXXXXXXX`) to add your phone number to
   emails, without putting it in the public repo.
9. **Test:** put the four values in `.env`, then run `python -m jobradar gmail-test`. A test draft addressed to
   yourself appears in Gmail → Drafts. Delete it afterwards.

The access only lets Job Radar **create drafts** and **read** mail (reading is for the next step: job-alert
emails and recruiter replies). It can't send. You can revoke it any time at <https://myaccount.google.com/permissions>.

## The app

### Sign in and sync (phone + laptop show the same thing)
The first time you open the app it asks you to sign in. That keeps your Applied / Interview / Skip choices identical
on every device, still for ₹0:

1. **Create a GitHub token** (once per device; the login page has the same steps):
   <https://github.com/settings/personal-access-tokens/new> → name `Job Radar`, expiration 1 year →
   *Only select repositories* → your Job-board repo → Permissions → Repository → **Contents: Read and write** → Generate.
2. **Choose a passphrase** on your first device (8+ characters) and type it twice. Use the same passphrase on every
   other device. It encrypts your statuses and never leaves the device.
3. Tick **Keep me signed in** so you don't have to sign in again.

Your statuses are stored in `sync/state.enc.json` in the repo, encrypted with AES-256-GCM (key from your passphrase,
PBKDF2-SHA256). The repo is public, but the file is unreadable without the passphrase. Changes reach your other
devices within a minute while the app is open, or straight away with ⟳ / pull down. Prefer not to sign in? Tap
*Use on this device only*.

### Using it
- **☰ menu:** your profile and totals, the sections with counts, Gmail sections, Export, Settings, theme, sign out.
- **Sections:** each job is in exactly one, and **Back** goes to the previous section (or closes the menu) instead
  of leaving the app.

  | Section | Buttons |
  |---|---|
  | 🎯 To apply | Apply · Applied · Later · Skip |
  | ⏳ Apply later | Apply · Applied · Skip · move back |
  | 🙈 Skipped | Apply · **Applied** (apply straight from here) · ↩ To apply |
  | ✅ Applied | View job · ✓ Applied (date) · move to Interview / Rejected / Undo |
  | 🗓️ Interview · 🎉 Offers · ✖️ Rejected | move on or back |
  | ✉️ Recruiter emails · 📥 Inbox updates | Gmail drafts, and what Gmail changed |

- **Colours:** blue **Apply** = not opened yet · amber **⏳ Pending** = you opened the posting but haven't confirmed
  (the card asks "Did you apply?") · green **✓ Applied** and a grey **View job** = done.
- **Gmail (read-only):** every 3 hours GitHub reads recent job emails. Application confirmations move the job to
  Applied, interview / test invites to Interview, rejections to Rejected, marked "from Gmail". Applications you made
  elsewhere (e.g. a LinkedIn confirmation for a job not in the list) are added to Applied too. Needs the Gmail setup
  below plus one more secret, `SYNC_PASSPHRASE` = the passphrase you sign in with.
- **Export to Excel:** ☰ → *Export to Excel (.csv)* (or Settings → This section). Status, applied date, score, title,
  company, location, source, skills, recruiter email and link for every job.
- Filters: minimum score plus multi-select chips (e.g. Naukri + LinkedIn); search; pull down to refresh; dark / light.

Preview it on your laptop with your local data:
```bash
python -m jobradar run
python -m http.server 8000 -d docs     # then open http://localhost:8000
```

## GitHub Actions and free limits

`.github/workflows/job-radar.yml` has three jobs:

| Job | When | What |
|---|---|---|
| fetch | every 3 hours (minute 17, UTC) | fetch + score, alert on strong new jobs, commit `docs/jobs.json` |
| deploy | after each fetch, or when you change files in `docs/` | publish `docs/` to GitHub Pages |
| notify | 08:55 IST daily, or by hand | daily summary, or a test notification |

- **Minutes:** public repos get unlimited free Actions minutes on standard runners. A fetch run takes a
  few minutes, so this would also fit easily in a private repo's 2,000 free minutes a month.
- **GitHub Pages** is free for public repos.
- **Scheduled runs** can start a few minutes late when GitHub is busy. That's normal.
- GitHub pauses schedules in repos with no activity for 60 days. The workflow's own commits count as
  activity, so this shouldn't happen. If it does, the Actions tab shows a button to re-enable it.
- If a run fails, GitHub emails you, and the run log shows each source's status.

## Quick start (local)

Needs Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m jobradar run --offline     # demo on bundled sample data, no network, no keys
python -m jobradar push-keys         # once: keys for phone alerts
python -m jobradar run               # the real thing: fetch everything once
python -m pytest                     # tests
```

`run` prints a per-source report and the top jobs, and writes `docs/jobs.json`.
`--offline` writes to `out/jobs.offline.json` instead, so sample data never ends up in the real file.

Other useful flags:

| Command | What it does |
|---|---|
| `python -m jobradar run --sources greenhouse,lever` | only some sources |
| `python -m jobradar run --no-write` | dry run, don't touch `docs/jobs.json` |
| `python -m jobradar run --force` | ignore `min_interval_hours` throttles |
| `python -m jobradar -v run` | verbose logging |
| `python -m jobradar check-companies` | test every company slug in `config.yaml` |
| `python -m jobradar run --notify` | also send phone alerts (needs the two push values in `.env`) |
| `python -m jobradar notify --test` | send a test notification |
| `python -m jobradar notify --summary` | send the daily summary now |
| `python -m jobradar run --fresh` | rebuild the job list from scratch |
| `python -m jobradar run --drafts` | also create Gmail drafts (needs the Gmail values in `.env`) |
| `python -m jobradar gmail-auth --client-file …json` | one-time Gmail sign-in |
| `python -m jobradar encrypt-resume resume.pdf` | encrypt your resume for draft attachments |
| `python -m jobradar gmail-test` | create a test draft to yourself |
| `python -m jobradar gmail-sync` | read job emails (read-only) and update statuses in the sync file |

### First thing to do: check the company list

`config.yaml` comes with 48 product companies, startups and MNCs across Greenhouse, Lever,
Ashby and SmartRecruiters. Companies switch ATS vendors now and then, so some slugs may be dead. Run:

```bash
python -m jobradar check-companies
```

It prints every company with its status, total open jobs, data-engineering jobs, and how many of
those are in India or remote. Delete or fix any `FAIL` rows. To find a company's slug, open its
careers page and look at the job links: `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`,
`jobs.ashbyhq.com/<slug>` or `jobs.smartrecruiters.com/<slug>`.

## LinkedIn, Naukri and Indeed

Neither LinkedIn nor Naukri offers a public job-search API, and their alert emails only show a
handful of jobs. Job Radar gets their listings with [JobSpy](https://github.com/speedyapply/JobSpy)
(`python-jobspy`, open source, free). It reads the same public search pages you see when logged out,
so no account or password is involved and your own LinkedIn/Naukri profiles can't be affected.

Configure it under `sources.jobspy` in `config.yaml`: sites, search queries, results per query,
and how many hours back to look. Each site × query is a separate call, so if LinkedIn rate-limits a
run you still get Naukri and Indeed, and the run report lists what failed.

Caveats:
- This is scraping. It's against LinkedIn's and Naukri's terms of use, and it can break when those
  sites change their pages. The version is pinned in `requirements.txt`. If a site stops returning
  jobs, upgrading `python-jobspy` is usually the fix.
- Sites sometimes block cloud server IPs (which is what GitHub Actions uses). Run
  `python -m jobradar run --sources jobspy` locally first, then watch the run report once it's on Actions.

If a site keeps blocking, the paid fallback is an [Apify](https://apify.com) scraper "actor". Apify's
free plan includes $5 of usage credit every month, and community Naukri/LinkedIn job actors are
priced at roughly $0.30 to $5 per 1,000 jobs. At this project's volume that should mostly fit inside the free credit.

## Free API keys (optional but recommended)

The four company-board APIs, JobSpy and Remotive need no key. Adzuna and Jooble need a free key.
Without one, that source is skipped and the rest still run.

For local runs, copy `.env.example` to `.env` and fill it in. `.env` is git-ignored.
(On GitHub the same values go into Secrets: see the one-time setup.)

### Adzuna
1. Go to <https://developer.adzuna.com/> and click **Register**.
2. Confirm your email and sign in. Your dashboard shows an **Application ID** and an **Application Key**.
3. Put them in `.env` as `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`.

The default config makes 8 Adzuna calls per run (2 queries × 4 cities). At 8 runs a day that's 64 calls,
well under the free quota.

### Jooble
1. Go to <https://jooble.org/api/about> and fill in the form (name, email, website: you can use your GitHub profile URL).
2. The key arrives by email.
3. Put it in `.env` as `JOOBLE_API_KEY`.

Jooble is throttled to once every 6 hours (`min_interval_hours` in `config.yaml`). So is Remotive,
which asks clients to call only a few times a day.

## Scoring

All rules live in `config.yaml`. No code changes are needed to tune them.

| Part | Max | Rule |
|---|---|---|
| Title | 25 | Best matching `title.rules` pattern (e.g. "data engineer" = 25, "ETL" = 18) |
| Skills | 35 | Sum of matched `skills.keywords` weights, capped. Weighted from your resume: ADF/Databricks 6, PySpark/ADLS 5, Azure/SQL 4, SCD, incremental load, metadata-driven, SHIR, DevOps... 3; Snowflake/BigQuery/Airflow 1 |
| Location | 20 | Anywhere in India 20, remote open to India 20. Abroad, or remote without India: dropped |
| Experience | 10 | Your band is **2–4 years**. A job fits if its range overlaps it ("1-3", "3-6", "4+" are kept; "5+", "6-8", "0-1" are dropped). Fits → 10, not stated → 5 |
| Recency | 10 | ≤1 day 10, ≤3 days 8, ≤7 days 6, ≤14 days 3, ≤30 days 1 |
| Penalty | | Staff/Principal −15, Lead/Architect −8, AWS/GCP-only stack with no Azure mention −12 |

Before scoring, a job must pass the **title gate**: its title has to match a `title.rules` pattern and
none of `title.exclude` (intern, manager, director, sales, data scientist/analyst...).

After scoring, a job is dropped if it is older than 30 days, scores below 40, isn't in India, or asks for
experience outside 2–4 years. **Walk-in drives** are kept only in Hyderabad (or when they're virtual/online),
and show a "walk-in" badge in the app. When a posting mentions several numbers, the largest requirement counts
("5+ years overall, 2+ with Databricks" means 5).
**India only:** a job is kept only if its location names an Indian city or state, or "India". A remote
job is kept only if it says India (e.g. "Remote - India"). "Remote (Worldwide)", "Remote - EMEA",
a bare "Remote", and every city abroad are dropped. Jobs already in `jobs.json` are re-checked
on every run, so tightening the rules also cleans out old entries. Jobs you've marked applied, interview or
offer are never dropped for age.

Each job records the keywords it matched (`matched_keywords`) and a `score_breakdown`, so you can see
why it scored what it did.

## Data file

`docs/jobs.json`:

```jsonc
{
  "generated_at": "2026-10-23T06:00:00+00:00",
  "stats": { "total": 42, "new_this_run": 3, "by_source": {...}, "dropped": {...}, "source_runs": {...} },
  "state": { "last_fetched": { "remotive": "..." } },   // throttle bookkeeping
  "jobs": [{
    "id": "a1b2c3d4e5f6",            // hash of company + title + city: stable across runs
    "title": "Azure Data Engineer", "company": "Visa", "location": "Hyderabad, TS, India",
    "remote": false, "source": "smartrecruiters", "url": "https://...",
    "posted_date": "2026-10-23T00:00:00+00:00", "first_seen": "2026-10-23T06:00:00+00:00",
    "description_snippet": "Design ADF v2 pipelines...",
    "score": 100, "matched_keywords": ["ADF", "Databricks", "PySpark"],
    "score_breakdown": { "title": 25, "skills": 35, "location": 20, ... },
    "status": "new"                  // new | applied | skipped | interview | rejected | offer
  }]
}
```

## Project layout

```
config.yaml            all tunables: profile, scoring rules, sources, company list
jobradar/
  __main__.py          CLI (run, check-companies)
  pipeline.py          fetch -> gate -> dedupe -> score -> filter -> merge -> save
  scoring.py           rule-based scorer
  models.py            the Job schema, text/date normalization
  sources/             one module per source (jobspy_boards.py = LinkedIn/Naukri/Indeed)
  offline.py           fixture-backed fake HTTP session for --offline and tests
tests/                 pytest suite + API-shaped fixtures
  push.py              Web Push encryption + VAPID signing (no third-party service)
  outreach.py          recruiter emails in postings, email + LinkedIn templates, drafting rules
  gmail.py             Gmail API over plain HTTP: sign-in, drafts
  resume.py            encrypted resume (assets/resume.pdf.enc)
  sync.py              encrypted status file shared with the app (same format as docs/sync.js)
  gmail_status.py      Gmail confirmations / interviews / rejections -> statuses
sync/state.enc.json    your statuses, encrypted (written by the app and by the workflow)
  notify.py            which notifications to send, and sending them
docs/                  the app (GitHub Pages root): index.html, app.js, sync.js, app.css, sw.js, jobs.json
.github/workflows/     the 3-hourly schedule
scripts/make_icons.py  regenerates the app icons
```

## Roadmap

1. ✅ Fetchers + scoring, run locally (+ LinkedIn, Naukri, Indeed; India-only)
2. ✅ Phone alerts from your own app (replaces the original Telegram plan)
3. ✅ GitHub Actions every 3 hours
4. ✅ The app on GitHub Pages
5. 🟡 Gmail: ✅ recruiter drafts with resume, ✅ LinkedIn recruiter search + notes,
   ⏳ reading LinkedIn/Naukri alert emails and recruiter replies / interview / rejection / offer detection
