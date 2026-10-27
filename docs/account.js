/* Job Radar accounts: each signed-in person's own profile, resume, matches,
   Gmail connection, review list and drafts, through the Job Radar API
   (app-config.json "api_url"). Without an API the app works as before from
   jobs.json. The API holds every secret; this file only ever sees the
   Firebase sign-in token, which it sends with each request.

   Plain script, loaded before app.js; it uses app.js helpers ($, el, toast...)
   only inside functions, after both files have loaded. */
"use strict";

const JRAccount = (() => {
  let me = null;           // GET /api/me
  let matchesCache = null;
  const TYPE_LABEL = {
    applied: "Application sent", acknowledged: "Application received", shortlisted: "Shortlisted",
    assessment: "Assessment / test", scheduling: "Scheduling", interview: "Interview", rejected: "Rejected",
    offer: "Offer", recruiter_reply: "Recruiter reply",
  };
  const REPLY_DRAFTS = {
    recruiter_reply: [["reply", "Draft reply"]],
    scheduling: [["scheduling", "Draft availability"]],
    interview: [["confirmation", "Draft confirmation"], ["thank_you", "Draft thank-you"]],
    shortlisted: [["scheduling", "Draft availability"]],
    assessment: [["reply", "Draft reply"]],
    offer: [["offer", "Draft offer reply"]],
  };
  const DRAFT_LABEL = { application: "Application", outreach: "Outreach", follow_up: "Follow-up", reply: "Reply",
    scheduling: "Availability", confirmation: "Confirmation", thank_you: "Thank-you", offer: "Offer reply" };

  const apiBase = () => String(appConfig.api_url || "").replace(/\/+$/, "");
  const enabled = () => !!(apiBase() && session);
  const profile = () => me?.profile || {};
  const draftsOn = () => enabled() && me?.features?.gmail && me?.gmail?.connected && profile().drafts_enabled !== false;

  const NEEDS_VERIFY = /verify your email/i;

  async function api(path, opts = {}, retried = false) {
    try {
      return await call(path, opts);
    } catch (e) {
      // The server read "not verified" from a cached token: ask Firebase again, retry once
      if (!retried && e.status === 403 && NEEDS_VERIFY.test(e.message) && (await checkVerification({ quiet: true }))) {
        return api(path, opts, true);
      }
      throw e;
    }
  }

  async function call(path, { method = "GET", body, raw, headers = {} } = {}) {
    const token = await JRCloud.idToken();
    const opts = { method, headers: { Authorization: `Bearer ${token}`, ...headers } };
    if (raw !== undefined) opts.body = raw;
    else if (body !== undefined) {
      opts.body = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
    }
    let r;
    try {
      r = await fetch(apiBase() + path, opts);
    } catch {
      throw new Error(navigator.onLine ? "Can't reach the Job Radar server." : "You're offline.");
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.detail || `Server error (${r.status})`), { status: r.status });
    return data;
  }

  async function refreshMe() {
    me = await api("/api/me");
    return me;
  }

  // ------------------------------------------------------------ sign-in
  async function onSignedIn() {
    if (!enabled()) return;
    try {
      await refreshMe();
    } catch (e) {
      toast(e.message);
      return;
    }
    // Back from the verification link (or the token is just stale): ask Firebase once
    if (!me.email_verified) await checkVerification({ quiet: true });
    renderBanner();
    handleReturn();
    if (me.email_verified && me.missing?.length) openOnboarding();
    load({ quiet: true });
    watchLive();
  }

  // Live updates: when the server re-scores your jobs, edits your profile (another device)
  // or syncs Gmail, the page refreshes that part by itself, no reload needed.
  let unwatch = [];
  function watchLive() {
    unwatch.forEach((u) => u());
    unwatch = [];
    if (!window.JRCloud?.watch || !session) return;
    const seen = {};
    const on = (col, run) => unwatch.push(JRCloud.watch(col, session.uid, (d) => {
      const v = d?.updated || d?.last_attempt || d?.connected_at || "";
      if (seen[col] === undefined) return void (seen[col] = v);  // first snapshot: what we already loaded
      if (v === seen[col]) return;
      seen[col] = v;
      run();
    }));
    on("matches", () => load({ quiet: true }));
    on("profiles", () => refreshMe().then(render).catch(() => null));
    on("gmail", () => refreshMe().then(render).catch(() => null));
  }

  // ------------------------------------------------------ email verification
  // Firebase is the only source of truth: the app never marks anyone verified itself.
  let verify = { state: "idle", checkedAt: 0, sentAt: 0, note: "" };
  const isVerified = () => !!me?.email_verified;

  async function checkVerification({ quiet = false } = {}) {
    if (!enabled() || verify.state === "checking") return isVerified();
    verify = { ...verify, state: "checking", note: "" };
    renderVerify();
    let fb = null;
    try {
      fb = await JRCloud.checkVerified();
      if (fb.email && session?.email && fb.email.toLowerCase() !== session.email.toLowerCase()) {
        verify.note = `This device is signed in as ${fb.email}. Open the link sent to that address.`;
      }
      me = await call("/api/me"); // what the server sees with the fresh token
      if (fb.verified && !me.email_verified) verify.note = "Firebase says verified, but the server didn't get the update yet. Tap Check again in a few seconds.";
    } catch (e) {
      if (/user-token-expired|user-not-found|user-disabled|no-current-user/.test(e.code || "")) {
        verify.note = "Your sign-in ended. Sign in again with the same email.";
      } else {
        verify.note = navigator.onLine ? "Couldn't check right now. Try again." : "You're offline.";
      }
    }
    verify.state = "idle";
    verify.checkedAt = Date.now();
    const ok = isVerified();
    if (ok) {
      verify.note = "";
      if (!quiet) toast("Email verified ✓");
    } else if (!quiet && !verify.note) {
      verify.note = "Not verified yet. Open the newest link we emailed you (check spam), then come back here.";
    }
    renderBanner();
    renderVerify();
    if (ok) unlockAfterVerify();
    return ok;
  }

  function unlockAfterVerify() {
    if ($("#onboard").open) openOnboarding();      // redraw: upload and save are now allowed
    else if (me?.missing?.length) openOnboarding();
    if ($("#settings").open) renderSettings();
  }

  async function resendVerification(button) {
    if (Date.now() - verify.sentAt < 60e3) return toast("A link was just sent. Wait a minute before asking for another.");
    button.disabled = true;
    try {
      await JRCloud.sendVerification();
      verify.sentAt = Date.now();
      verify.note = `New link sent to ${me?.email || session?.email}. Use the newest email if you got several.`;
      toast("Verification email sent. Check your inbox (and spam).");
    } catch (e) {
      verify.note = e?.code === "auth/too-many-requests" ? "Too many emails sent. Wait a few minutes, then try again." : authMessage(e) || "Couldn't send it. Try again in a minute.";
    } finally {
      button.disabled = false;
      renderVerify();
    }
  }

  // A status box, shown in the banner, the profile sheet and settings while pending
  function verifyBox() {
    const box = el("div", { className: "verify-box" });
    box.dataset.verify = "1";
    fillVerifyBox(box);
    return box;
  }
  function fillVerifyBox(box) {
    if (isVerified()) {
      box.replaceChildren(el("span", { className: "st-chip st-applied", textContent: "Email verified" }));
      return;
    }
    const checking = verify.state === "checking";
    const check = el("button", { className: "btn btn-primary", type: "button", textContent: checking ? "Checking…" : "I've verified my email — Check again" });
    check.disabled = checking;
    check.addEventListener("click", () => checkVerification());
    const resend = el("button", { className: "btn", type: "button", textContent: "Send a new link" });
    resend.addEventListener("click", () => resendVerification(resend));
    box.replaceChildren(...[
      el("div", { className: "panel-top" }, el("b", { textContent: me?.email || session?.email || "" }),
        el("span", { className: "st-chip st-pending", textContent: checking ? "checking…" : "Email verification pending" })),
      el("p", { className: "muted", textContent: "Open the link we emailed you, then come back to this page. Resume upload, Gmail and drafts unlock once your email is verified." }),
      verify.note ? el("p", { className: "note", role: "status", textContent: verify.note }) : null,
      el("div", { className: "row" }, check, resend)].filter(Boolean));
  }
  function renderVerify() {
    for (const box of document.querySelectorAll("[data-verify]")) fillVerifyBox(box);
  }

  // Coming back to the tab (e.g. from the email) re-checks; "focus" and "visibilitychange"
  // usually fire together, so the second one within 1.5 seconds is skipped
  function onReturnToApp() {
    if (document.visibilityState !== "visible" || !enabled() || !me || isVerified()) return;
    if (Date.now() - verify.checkedAt < 1500) return;
    checkVerification({ quiet: true }).then((ok) => ok && toast("Email verified ✓"));
  }
  document.addEventListener("visibilitychange", onReturnToApp);
  window.addEventListener("focus", onReturnToApp);

  function renderBanner() {
    const bar = $("#acct-banner");
    if (!enabled() || !me) return (bar.hidden = true);
    if (!me.email_verified) {
      bar.replaceChildren(verifyBox());
      return (bar.hidden = false);
    }
    if (me.missing?.length) {
      const go = el("button", { className: "link-btn", type: "button", textContent: "Finish setup" });
      go.addEventListener("click", openOnboarding);
      bar.replaceChildren(el("span", { textContent: "Add your resume or profile so Job Radar can find jobs for you." }), go);
      return (bar.hidden = false);
    }
    bar.hidden = true;
  }

  // Google sends you back from the Gmail consent screen with #gmail=connected|cancelled|error
  function handleReturn() {
    const q = new URLSearchParams(location.hash.slice(1));
    const st = q.get("gmail");
    if (!st) return;
    history.replaceState(history.state, "", location.pathname + location.search);
    toast(st === "connected" ? "Gmail connected ✓" : st === "cancelled" ? "Gmail wasn't connected." : q.get("message") || "Gmail wasn't connected.");
    if (st === "connected") refreshMe().then(() => syncGmail(true)).catch(() => null);
  }

  // -------------------------------------------------------------- matches
  // While the server searches for jobs, check back every 20 seconds and show them when ready
  let pollTimer = null, wasSearching = false;
  function watchSearch(m) {
    clearTimeout(pollTimer);
    if (m.searching) {
      wasSearching = true;
      pollTimer = setTimeout(() => load({ quiet: true }), 20e3);
    } else if (wasSearching) {
      wasSearching = false;
      toast(m.search_error ? "The job search hit a problem; it will try again on the next run."
        : `Job search finished · ${(m.jobs || []).length} matches for you`);
    }
  }

  // ⟳ and pull-to-refresh: re-score against the newest jobs (and search if the list is old)
  async function refreshNow() {
    try {
      await api("/api/matches/refresh", { method: "POST" });
    } catch (e) {
      if (![409, 429].includes(e.status)) toast(e.message);
    }
    await load({ quiet: true });
  }

  async function loadMatches() {
    const m = await api("/api/matches");
    matchesCache = m;
    watchSearch(m);
    const p = profile();
    return {
      jobs: m.jobs || [], generated_at: m.updated, alert_score: p.threshold ?? 70,
      searching: !!m.searching, search_started: m.search_started, pool_count: m.pool_count,
      sources_available: m.sources_available || [], run: m.run || null, funnel: m.funnel || null,
      // the server stores {skill, count} objects; the page works with [skill, count] pairs
      skills_scan: m.skills_scan ? { ...m.skills_scan, skills: (m.skills_scan.skills || []).map((x) => (Array.isArray(x) ? x : [x.skill, x.count])) } : null,
      outreach: { profile: { name: (p.full_name || "").split(" ")[0], full_name: p.full_name, role: p.current_role,
        years_experience: p.years_experience, notice: p.notice, linkedin: p.linkedin }, my_skills: p.skills || [] },
    };
  }

  function decorateCard(job, card) {
    if (!enabled()) return;
    const m = job.match;
    if (m) {
      // Missing skills, each with + to add it to your profile (then it counts as matched on every job)
      if (m.missing_skills?.length) {
        const ul = el("ul", { className: "keywords missing" }, ...m.missing_skills.slice(0, 8).map((k) => {
          const b = el("button", { type: "button", className: "add-skill", textContent: `+ ${k}`, title: `I have ${k}: add it to my skills` });
          b.addEventListener("click", () => JRDesk.editSkill("add", k));
          return el("li", {}, b);
        }));
        card.querySelector(".keywords").after(ul);
      }
      card.querySelector(".snippet").title = m.summary || "";
    }
    const status = statusOf(job);
    if (job.source === "email" || !["new", "later", "skipped", "applied", "interview"].includes(status)) return;
    const row = card.querySelector(".outreach-row");
    // Works without Gmail or a recruiter email: the editor lets you copy, download or save to Gmail
    const kinds = ["applied", "interview"].includes(status) ? [["follow_up", "✉️ Follow up"]]
      : [["application", "✉️ Draft application"], ["outreach", "🤝 Draft outreach"]];
    for (const [kind, label] of kinds) {
      const done = store.drafts[`${kind}:${job.id}`];
      const b = el("button", { className: `mini-btn${done ? " mail" : ""}`, type: "button", textContent: done ? `${label} ✓` : label });
      if (done) b.title = "A Gmail draft already exists; open to copy or review";
      b.addEventListener("click", () => openDraft(job, kind));
      row.append(b);
    }
  }

  // ------------------------------------------------------- draft editor
  const EMAIL_OK = /^[^@\s<>]+@[^@\s<>]+\.[a-z]{2,24}$/i;

  async function openDraft(job, kind) {
    const dlg = $("#draft-dlg"), body = $("#draft-body");
    $("#draft-title").textContent = { application: "Application", outreach: "Recruiter outreach", follow_up: "Follow-up" }[kind];
    body.replaceChildren(el("p", { className: "muted", textContent: "Writing from your profile…" }));
    if (!dlg.open) openOverlay(dlg);
    let p;
    try {
      p = await api("/api/drafts/preview", { method: "POST", body: { type: kind, job_id: job.id } });
    } catch (e) {
      body.replaceChildren(el("p", { className: "lg-error", textContent: e.message }));
      return;
    }
    const field = (label, node, hint) => el("label", { className: "lg-field" }, el("span", {}, label,
      hint ? el("small", { className: "muted", textContent: ` · ${hint}` }) : null), node);
    const copyBtn = (label, get, ok) => {
      const b = el("button", { className: "btn", type: "button", textContent: label });
      b.addEventListener("click", () => copyText(get(), ok));
      return b;
    };
    const parts = [el("p", { className: "draft-job" }, el("b", { textContent: job.title }), ` · ${job.company}`)];

    // 1. Applying: the real application link first, clearly separate from email
    if (kind === "application") {
      const a = p.apply || {};
      const href = safeUrl(a.url);
      const label = a.type === "company_site" ? "Apply on the company's careers site ↗" : a.type === "external" ? "Open the application page ↗" : "Open the job listing ↗";
      parts.push(el("div", { className: "draft-box" },
        el("b", { textContent: "Apply" }),
        el("p", { className: "muted", textContent: `${applyLabel(a.type || "unknown")}${a.evidence ? ": " + a.evidence : ""}. Opening it doesn't mark the job as applied: tap Applied once you've submitted.` }),
        href ? el("div", { className: "row" }, el("a", { className: "btn btn-primary", href, target: "_blank", rel: "noopener noreferrer", textContent: label })) : null));
    }
    if (kind === "follow_up" && p.too_soon) parts.push(el("p", { className: "note", textContent: p.advice }));
    if (p.existing_draft) parts.push(el("p", { className: "note" }, `A ${DRAFT_LABEL[kind] || kind} draft is already in your Gmail (${ago(p.existing_draft.at)}). `,
      el("a", { href: "https://mail.google.com/mail/u/0/#drafts", target: "_blank", rel: "noopener", textContent: "Open Gmail drafts ↗" })));

    // 2. Outreach without an email address: LinkedIn note and where to find the recruiter
    if (kind === "outreach") {
      const note = el("textarea", { rows: 3, value: p.linkedin_note || "", maxLength: 200 });
      const count = el("small", { className: "muted", textContent: `${note.value.length}/200` });
      note.addEventListener("input", () => (count.textContent = `${note.value.length}/200`));
      parts.push(el("div", { className: "draft-box" }, el("b", { textContent: "LinkedIn" }),
        field("Connection note", note), count,
        el("div", { className: "row" }, copyBtn("Copy note", () => note.value, "Note copied. LinkedIn: Connect → Add a note → paste"),
          el("a", { className: "btn", href: p.recruiter_search, target: "_blank", rel: "noopener", textContent: "Find the recruiter ↗" }),
          p.careers_link ? el("a", { className: "btn", href: safeUrl(p.careers_link), target: "_blank", rel: "noopener", textContent: "Company careers page ↗" }) : null)));
    }

    // 3. The email (or message to paste anywhere)
    const to = el("input", { type: "email", placeholder: "Optional: recruiter's email", autocomplete: "off" });
    const toInfo = el("small", { className: "muted" });
    const contacts = p.contacts || [];
    const who = el("input", { placeholder: "Optional: their name, e.g. Priya Sharma", autocomplete: "off", maxLength: 60 });
    const whoInfo = el("small", { className: "muted" });
    const NAME_SRC = { posting: "named in the job post", address: "read from the email address", gmail: "from your Gmail" };
    let msg = null;
    const greet = () => {   // keep the message's first line in step with the name
      if (!msg) return;
      const first = who.value.trim().split(/\s+/)[0];
      msg.value = msg.value.replace(/^(Hi|Dear)[^\n]*,/, first ? `Hi ${first},` : "Hi,");
    };
    who.addEventListener("input", () => { whoInfo.textContent = who.value.trim() ? "Used in “Hi …,” and the To line." : ""; greet(); });
    const checkTo = () => {
      const v = to.value.trim();
      const found = contacts.find((c) => c.email.toLowerCase() === v.toLowerCase());
      if (found?.name && !who.dataset.typed) {
        who.value = found.name;
        whoInfo.textContent = `Name ${NAME_SRC[found.name_source] || "found"}. Check it before sending.`;
        greet();
      }
      toInfo.textContent = !v ? "Leave empty to add the recipient in Gmail later."
        : !EMAIL_OK.test(v) ? "That doesn't look like a valid email address."
        : found ? `Found in the job posting.` : "Added by you: not verified by Job Radar.";
      toInfo.classList.toggle("lg-error", !!v && !EMAIL_OK.test(v));
    };
    to.addEventListener("input", checkTo);
    const chips = contacts.length ? el("div", { className: "chips" }, ...contacts.map((c) => {
      const b = el("button", { type: "button", className: "chip" }, c.name ? `${c.name} · ${c.email}` : c.email,
        el("span", { className: "chip-count", textContent: c.source }));
      b.addEventListener("click", () => { to.value = c.email; checkTo(); });
      return b;
    })) : el("small", { className: "muted", textContent: "No recruiter email in this posting. You can still copy the message, or create the Gmail draft and add the address there." });
    checkTo();
    who.addEventListener("keydown", () => (who.dataset.typed = "1"));
    const subject = el("input", { value: p.subject });
    msg = el("textarea", { rows: 10, value: p.body });
    parts.push(el("div", { className: "draft-box" }, el("b", { textContent: kind === "outreach" ? "Email to the recruiter" : "Email" }),
      field("To", to), toInfo, chips, field("Recruiter name", who, "only if you know it"), whoInfo,
      field("Subject", subject), field("Message", msg, "edit freely"),
      el("div", { className: "row" }, copyBtn("Copy message", () => `${subject.value}\n\n${msg.value}`, "Message copied"))));

    // 4. Cover letter (application)
    let letter = null;
    if (kind === "application" && p.cover_letter) {
      letter = el("textarea", { rows: 12, value: p.cover_letter });
      const dl = el("button", { className: "btn", type: "button", textContent: "Download (.txt)" });
      dl.addEventListener("click", () => download(`Cover letter - ${job.company}.txt`.replace(/[\\/:*?"<>|]+/g, ""), new Blob([letter.value], { type: "text/plain" })));
      parts.push(el("div", { className: "draft-box" }, el("b", { textContent: "Cover letter" }), field("Cover letter", letter, "edit freely"),
        el("div", { className: "row" }, copyBtn("Copy cover letter", () => letter.value, "Cover letter copied"), dl)));
    }

    // 5. Gmail draft (never sent)
    const save = el("button", { className: "btn btn-primary", type: "button", textContent: "Create Gmail draft" });
    save.addEventListener("click", async () => {
      const v = to.value.trim();
      if (v && !EMAIL_OK.test(v)) return toast("Fix the email address, or leave it empty.");
      const name = who.value.trim();
      if (name && !/^[A-Za-z][A-Za-z .'-]*$/.test(name)) return toast("Use only letters, spaces, dots, hyphens or apostrophes in the name.");
      await createDraft({ type: kind, job_id: job.id, to: v, to_name: name, subject: subject.value, body: msg.value }, save);
    });
    parts.push(el("div", { className: "draft-box" }, el("b", { textContent: "Gmail" }),
      draftsOn() ? el("p", { className: "muted", textContent: "Saved as a draft in your Gmail with your resume attached. Job Radar never sends it: you review and send it yourself." })
        : el("p", { className: "muted", textContent: me?.gmail?.connected ? "Gmail drafts are turned off in your profile settings. You can still copy everything above."
          : "Connect Gmail in Settings to save this as a Gmail draft. You can still copy everything above." }),
      draftsOn() ? el("div", { className: "row" }, save) : null));
    body.replaceChildren(...parts.filter(Boolean));
  }

  async function createDraft(body, button) {
    if (button) button.disabled = true;
    try {
      const { draft } = await api("/api/drafts", { method: "POST", body });
      toast(draft.duplicate ? "That draft already exists in Gmail." : "Draft created in Gmail. Review it there before sending.",
        { label: "Open Gmail", run: () => window.open("https://mail.google.com/mail/u/0/#drafts", "_blank", "noopener") });
      // The server recorded it in your synced state; the live listener brings it in, this shows it now
      const key = ["reply", "scheduling", "confirmation", "thank_you", "offer"].includes(body.type) && draft.thread_id
        ? `${body.type}:${draft.thread_id}` : `${body.type}:${body.job_id}`;
      store.drafts[key] = draft;
      saveLocal();
      render();
    } catch (e) {
      toast(e.message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ------------------------------------------------------------------ Gmail
  async function syncGmail(quiet = false) {
    try {
      const r = await api("/api/gmail/sync", { method: "POST" });
      if (!quiet) toast(`Gmail checked · ${r.report.checked || 0} emails read, ${r.report.updated || 0} updated, ${r.report.review || 0} to review`
        + (r.report.partial ? ` · ${r.report.remaining} more next sync` : ""));
    } catch (e) {
      if (!quiet) toast(e.message);
    }
    await refreshMe().catch(() => null);
    render();
    if ($("#settings").open) renderSettings();
  }

  async function connectGmail() {
    try {
      const { url } = await api("/api/gmail/connect", { method: "POST", body: { return_to: window.JRNative?.isApp ? "android" : "web" } });
      if (window.JRNative?.isApp) window.JRNative.openExternal(url);
      else location.href = url;
    } catch (e) {
      toast(e.message);
    }
  }

  async function disconnectGmail() {
    if (!confirm("Disconnect Gmail? Job Radar's access is revoked at Google and its stored key deleted. Your job statuses stay.")) return;
    try {
      await api("/api/gmail/disconnect", { method: "POST" });
      toast("Gmail disconnected");
    } catch (e) {
      toast(e.message);
    }
    await refreshMe().catch(() => null);
    render();
    renderSettings();
  }

  function gmailCard() {
    const g = me?.gmail || {};
    const box = el("div", { className: "gm-card" });
    if (!me?.features?.gmail) {
      box.append(el("p", { className: "muted", textContent: "Gmail isn't available on this server yet." }));
      return box;
    }
    if (!g.connected) {
      const btn = el("button", { className: "btn btn-primary", type: "button", textContent: "Connect Gmail" });
      btn.addEventListener("click", connectGmail);
      box.append(...[
        el("p", { className: "muted", textContent: "Separate from signing in. Job Radar asks Google for two permissions: read job-related emails (to update your applications) and create drafts (it never sends anything)." }),
        g.last_error ? el("p", { className: "lg-error", textContent: g.last_error }) : null,
        el("div", { className: "row" }, btn)].filter(Boolean));
      return box;
    }
    const sync = el("button", { className: "btn btn-primary", type: "button", textContent: "Sync now" });
    sync.addEventListener("click", async () => {
      sync.disabled = true;
      sync.textContent = "Checking…";
      await syncGmail();
    });
    const off = el("button", { className: "btn", type: "button", textContent: "Disconnect" });
    off.addEventListener("click", disconnectGmail);
    const rep = g.last_report || {};
    box.append(...[
      el("div", { className: "panel-top" }, el("b", { textContent: g.email || "Gmail" }), el("span", { className: "st-chip st-applied", textContent: "connected" })),
      el("p", { className: "muted", textContent: g.last_success ? `Last successful sync ${ago(g.last_success)} · ${rep.checked ?? 0} emails read, ${rep.updated ?? 0} updates, ${rep.review ?? 0} to review` : "Not synced yet." }),
      g.note && !g.last_error ? el("p", { className: "muted", textContent: g.note }) : null,
      g.last_error ? el("p", { className: "lg-error", textContent: `${g.last_error}${g.last_attempt ? " (" + ago(g.last_attempt) + ")" : ""}` }) : null,
      el("div", { className: "row" }, sync, off)].filter(Boolean));
    return box;
  }

  function resolveReview(item, jobId) {
    const now = new Date().toISOString();
    store.reviews[item.id] = { job_id: jobId, at: now };
    store.gmail = { ...store.gmail, review: (store.gmail.review || []).filter((r) => r.id !== item.id) };
    if (jobId) {
      const job = allJobs().find((j) => j.id === jobId) || item.candidates.find((c) => c.id === jobId);
      const rank = { new: 0, later: 0, skipped: 0, applied: 1, interview: 2, rejected: 3, offer: 4 };
      if (job && (rank[item.status] || 0) > (rank[statusOf(job)] || 0)) setStatus(job, item.status);
      else changed();
      toast(`Matched to ${job?.title || "the job"} at ${job?.company || ""}`);
    } else {
      changed();
      toast("Ignored");
    }
    render();
  }

  function renderInbox() {
    const wrap = el("div", { className: "panel-list" });
    wrap.append(gmailCard());
    const review = (store.gmail.review || []).filter((r) => !store.reviews[r.id]);
    if (review.length) {
      wrap.append(el("h3", { className: "panel-h", textContent: `Check these (${review.length})` }),
        el("p", { className: "muted", textContent: "Job Radar wasn't sure which application these emails belong to. Nothing changes until you choose." }));
      for (const r of review) {
        const choices = el("div", { className: "row" });
        for (const c of r.candidates || []) {
          const b = el("button", { className: "btn", type: "button", textContent: `${c.title} · ${c.company}` });
          b.addEventListener("click", () => resolveReview(r, c.id));
          choices.append(b);
        }
        const none = el("button", { className: "btn", type: "button", textContent: "None of these" });
        none.addEventListener("click", () => resolveReview(r, null));
        choices.append(none);
        wrap.append(el("article", { className: "panel-item review" },
          el("div", { className: "panel-top" }, el("b", { textContent: r.company || r.from }),
            el("span", { className: `st-chip st-${r.status}`, textContent: TYPE_LABEL[r.type] || r.type })),
          el("p", { textContent: r.subject }),
          el("p", { className: "muted", textContent: `${fmtDate(r.at)} · from ${r.from} · ${r.reason}` }), choices));
      }
    }
    const all = store.gmail.events || [];
    const isDone = (ev) => !!store.done[ev.id]?.on;
    const events = [...all].sort((a, b) => isDone(a) - isDone(b) || String(b.at).localeCompare(String(a.at)));
    const open = all.filter((ev) => !isDone(ev)).length;
    wrap.append(el("h3", { className: "panel-h", textContent: `Needs your reply · ${open} open` }),
      el("p", { className: "muted", textContent: "Job emails from your Gmail, sorted open first. Only the subject's meaning is shown, never the email text." }));
    if (!events.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No job emails yet" }),
        "Confirmations, tests, interview invites, rejections, offers and recruiter replies appear here."));
      return wrap;
    }
    for (const ev of events.slice(0, 80)) {
      const row = el("div", { className: "row" });
      if (ev.job_id) {
        const show = el("button", { className: "btn", type: "button", textContent: "Show job" });
        show.dataset.safe = "1";
        show.addEventListener("click", () => focusJob(ev.job_id));
        row.append(show);
      }
      if (draftsOn()) {
        for (const [kind, label] of REPLY_DRAFTS[ev.type] || []) {
          const rec = store.drafts[`${kind}:${ev.thread_id}`];
          if (rec) {
            row.append(el("a", { className: "btn", href: gmailDraftUrl(rec), target: "_blank", rel: "noopener", textContent: `${DRAFT_LABEL[kind]} draft ready ↗` }));
          } else {
            const b = el("button", { className: "btn", type: "button", textContent: label });
            b.addEventListener("click", () => createDraft({ type: kind, message_id: ev.id, job_id: ev.job_id || undefined }, b));
            row.append(b);
          }
        }
      }
      const done = isDone(ev);
      const box = el("input", { type: "checkbox", checked: done, className: "done-box" });
      box.setAttribute("aria-label", `Mark ${ev.company || ev.from} ${done ? "not done" : "done"}`);
      box.addEventListener("change", () => {
        store.done[ev.id] = { on: box.checked, at: new Date().toISOString() };
        changed();
        render();
        toast(box.checked ? "Marked done" : "Marked open again");
      });
      const cat = INBOX_CAT[ev.type] || "Action";
      const summary = `${TYPE_LABEL[ev.type] || ev.type}${ev.title ? `: ${ev.title}` : ""}${ev.applied ? " · status updated" : ""}`;
      const item = el("article", { className: `panel-item inbox-item${done ? " is-done" : ""}` },
        el("div", { className: "panel-top" },
          el("label", { className: "done-wrap" }, box, el("b", { textContent: ev.company || ev.from })),
          el("span", { className: `type-badge type-${cat.toLowerCase()}`, textContent: cat })),
        el("p", { textContent: summary, title: ev.subject || "" }),
        el("p", { className: "muted", textContent: `${ev.from ? `${ev.from} · ` : ""}Gmail · ${fmtDate(ev.at)}` }),
        row.children.length ? row : null);
      wrap.append(item);
    }
    return wrap;
  }

  // ------------------------------------------------------ recruiter emails
  const INBOX_CAT = { applied: "Applied", acknowledged: "Applied", rejected: "Rejected", recruiter_reply: "Recruiter",
    shortlisted: "Action", assessment: "Action", scheduling: "Action", interview: "Action", offer: "Action" };
  const JOB_DRAFTS = ["application", "outreach"];
  const STAGES = ["draft", "nodraft", "sent", "followup", "replied", "closed"];
  const STAGE_LABEL = { draft: "Draft ready", nodraft: "No draft yet", sent: "Sent", followup: "Follow-up sent", replied: "Replied", closed: "Closed" };
  const gmailDraftUrl = (rec) => (rec?.message_id
    ? `https://mail.google.com/mail/u/0/#drafts?compose=${encodeURIComponent(rec.message_id)}` : "https://mail.google.com/mail/u/0/#drafts");
  // Working days (Mon–Fri) from `iso` to now
  function workingDaysSince(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return 0;
    let n = 0;
    const end = startOfToday();
    d.setHours(0, 0, 0, 0);
    while (d < end) {
      d.setDate(d.getDate() + 1);
      if (d.getDay() !== 0 && d.getDay() !== 6) n++;
    }
    return n;
  }
  const FOLLOW_UP_DAYS = 5;
  const stageOf = (rec) => (!rec ? "nodraft" : rec.closed ? "closed" : rec.replied ? "replied" : rec.followup_sent ? "followup" : rec.sent ? "sent" : "draft");
  const isDue = (rec) => stageOf(rec) === "sent" && workingDaysSince(rec.sent) >= FOLLOW_UP_DAYS;

  function setDraft(key, patch, msg) {
    if (readOnly) return toast(`Not saved: ${readOnly}`);
    store.drafts[key] = { ...store.drafts[key], ...patch, at: new Date().toISOString() };
    changed();
    render();
    toast(msg);
  }

  function recruiterEntries() {
    const jobs = new Map(allJobs().map((j) => [j.id, j]));
    const out = new Map();
    for (const [key, rec] of Object.entries(store.drafts || {})) {
      if (!JOB_DRAFTS.includes(rec.type) || !rec.job_id) continue;
      const cur = out.get(rec.job_id);
      if (!cur || rec.type === "outreach") out.set(rec.job_id, { key, rec, job: jobs.get(rec.job_id) });
    }
    for (const j of jobs.values()) {
      if (!out.has(j.id) && (j.contact_emails || []).length && statusOf(j) !== "skipped") out.set(j.id, { key: null, rec: null, job: j });
    }
    return [...out.values()].map((e) => ({ ...e, stage: stageOf(e.rec), due: isDue(e.rec),
      followUp: store.drafts[`follow_up:${e.rec?.job_id || e.job?.id}`] }));
  }

  function morningBox() {
    const p = profile();
    const last = me?.gmail?.last_auto;
    if (!p.auto_drafts) {
      const on = el("button", { className: "link-btn", type: "button", textContent: "Turn on morning recruiter drafts" });
      on.addEventListener("click", () => openOnboarding());
      return el("div", { className: "note" }, "Want drafts made for you every morning at 9? ", on);
    }
    const run = el("button", { className: "btn", type: "button", textContent: "Run now" });
    run.addEventListener("click", async () => {
      await withBusy(run, "Working…", async () => {
        try {
          const { report: r } = await api("/api/drafts/auto", { method: "POST" });
          toast(`${r.created} new draft${r.created === 1 ? "" : "s"}, ${r.follow_ups} follow-up${r.follow_ups === 1 ? "" : "s"}` +
            (r.sent || r.replied ? ` · noticed ${r.sent} sent, ${r.replied} replied` : "") + (r.created ? " · open Gmail Drafts to send" : ""));
        } catch (e) {
          toast(e.message);
        }
        await refreshMe().catch(() => null);
        render();
      });
    });
    const summary = !last ? "Runs every morning at 9:00. Nothing yet."
      : last.error ? `Last run ${ago(last.at)} stopped: ${last.error}`
      : `Last run ${ago(last.at)}: ${last.created} new draft${last.created === 1 ? "" : "s"}, ${last.follow_ups} follow-up${last.follow_ups === 1 ? "" : "s"}` +
        `${last.sent || last.replied ? `, noticed ${last.sent} sent and ${last.replied} replied` : ""}. ${last.candidates ? `${last.candidates} recruiter email${last.candidates === 1 ? " was" : "s were"} waiting.` : ""}`;
    return el("div", { className: "note morning" }, el("b", { textContent: `☀️ Morning drafts on · up to ${p.auto_draft_limit ?? 5} a day` }),
      el("p", { textContent: summary }), el("div", { className: "row" }, run));
  }

  function renderDrafts() {
    const wrap = el("div", { className: "panel-list" });
    const entries = recruiterEntries().sort((a, b) => (b.due - a.due) || STAGES.indexOf(a.stage) - STAGES.indexOf(b.stage) ||
      String(b.rec?.at || b.job?.first_seen || "").localeCompare(String(a.rec?.at || a.job?.first_seen || "")));
    const toReview = entries.filter((e) => e.stage === "draft").length, due = entries.filter((e) => e.due).length;
    wrap.append(el("div", { className: "mail-summary" },
      el("span", { className: "stat-pill" }, el("b", { textContent: toReview }), ` draft${toReview === 1 ? "" : "s"} to review`),
      el("span", { className: `stat-pill${due ? " warn" : ""}` }, el("b", { textContent: due }), ` follow-up${due === 1 ? "" : "s"} due`)),
    el("p", { className: "muted", textContent: draftsOn() ? `Drafts are created in your Gmail and never sent by Job Radar. A follow-up is due ${FOLLOW_UP_DAYS} working days after you send one.`
      : me?.gmail?.connected ? "Drafts are turned off in Settings." : "Connect Gmail in Settings to create drafts. You can still copy them from the editor." }),
    morningBox());
    if (!entries.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No recruiter emails yet" }),
        "When a job lists a recruiter's email, or you create a draft with ✉️ on a job, it shows up here."));
    }
    for (const e of entries) {
      const { job, rec, key } = e;
      const to = rec?.to || (job?.contact_emails || [])[0] || "";
      const yrs = job?.score_breakdown?.years_required;
      const gap = job ? JRDesk.fitLines(job)[1] : "";
      const row = el("div", { className: "row" });
      const btn = (label, run, primary) => {
        const b = el("button", { className: `btn${primary ? " btn-primary" : ""}`, type: "button", textContent: label });
        b.addEventListener("click", run);
        row.append(b);
      };
      if (rec?.id) row.append(el("a", { className: "btn btn-primary", href: gmailDraftUrl(rec), target: "_blank", rel: "noopener", textContent: "Open draft ↗" }));
      else if (job) btn("✉️ Draft email", () => openDraft(job, "outreach"), true);
      if (e.followUp?.id) row.append(el("a", { className: "btn btn-primary", href: gmailDraftUrl(e.followUp), target: "_blank", rel: "noopener", textContent: "Open follow-up ↗" }));
      else if (e.due && job) btn("✉️ Draft follow-up", () => openDraft(job, "follow_up"), true);
      const url = safeUrl(job?.url);
      if (url) row.append(el("a", { className: "btn", href: url, target: "_blank", rel: "noopener", textContent: "Recruiter post ↗" }));
      if (rec) {
        const now = new Date().toISOString();
        if (e.stage === "draft") btn("Mark sent", () => setDraft(key, { sent: now }, `Marked sent · follow-up due in ${FOLLOW_UP_DAYS} working days`));
        if (e.stage === "sent") btn("Follow-up sent", () => setDraft(key, { followup_sent: now }, "Follow-up marked sent"));
        if (["sent", "followup"].includes(e.stage)) btn("Replied", () => setDraft(key, { replied: now }, "Marked replied"));
        if (e.stage !== "closed") btn("Close", () => setDraft(key, { closed: now }, "Closed"));
        else btn("Reopen", () => setDraft(key, { closed: null }, "Reopened"));
        const note = el("button", { className: "link-btn", type: "button", textContent: rec.note ? "Edit note" : "Add note" });
        note.addEventListener("click", () => {
          const v = prompt("Note (only you see it):", rec.note || "");
          if (v !== null) setDraft(key, { note: v.trim().slice(0, 300) }, "Note saved");
        });
        row.append(note);
      }
      const when = rec?.closed ? `closed ${fmtDate(rec.closed)}` : rec?.replied ? `replied ${fmtDate(rec.replied)}`
        : rec?.followup_sent ? `follow-up sent ${fmtDate(rec.followup_sent)}` : rec?.sent ? `sent ${fmtDate(rec.sent)}` : rec ? `drafted ${ago(rec.at)}` : "";
      wrap.append(el("article", { className: `panel-item${e.due ? " due" : ""}` },
        el("div", { className: "panel-top" }, el("b", { textContent: job?.company || to || "Recruiter" }),
          el("span", { className: "chip-row" }, rec?.auto ? el("span", { className: "badge", textContent: "auto", title: "Made by the 9 AM run" }) : null,
            el("span", { className: `st-chip mail-${e.stage}`, textContent: e.due ? "Follow-up due" : STAGE_LABEL[e.stage] }))),
        el("p", { textContent: [job?.title, rec ? DRAFT_LABEL[rec.type] : null].filter(Boolean).join(" · ") }),
        el("p", { className: "muted", textContent: [to ? `To ${rec?.to_name ? `${rec.to_name} <${to}>` : to}` : "No recruiter email in the post", job ? locationLabel(job) : "",
          yrs != null ? `${yrs}${job.score_breakdown.years_max ? `–${job.score_breakdown.years_max}` : "+"} yrs` : "", when,
          rec?.attached ? "resume attached" : ""].filter(Boolean).join(" · ") }),
        gap ? el("p", { className: "gap-line", textContent: gap }) : null,
        rec?.note ? el("p", { className: "note-line", textContent: `Note: ${rec.note}` }) : null,
        row));
    }
    // Replies, availability, thank-yous… (drafted from Inbox updates)
    const other = Object.values(store.drafts || {}).filter((d) => !JOB_DRAFTS.includes(d.type) && d.type !== "follow_up")
      .sort((a, b) => String(b.at).localeCompare(String(a.at)));
    if (other.length) {
      wrap.append(el("h3", { className: "panel-h", textContent: `Other drafts (${other.length})` }));
      for (const d of other) {
        const job = d.job_id && allJobs().find((j) => j.id === d.job_id);
        wrap.append(el("article", { className: "panel-item" },
          el("div", { className: "panel-top" }, el("b", { textContent: job?.company || d.to }), el("span", { className: "st-chip st-interview", textContent: DRAFT_LABEL[d.type] || d.type })),
          el("p", { textContent: d.subject }),
          el("div", { className: "row" }, el("a", { className: "btn", href: gmailDraftUrl(d), target: "_blank", rel: "noopener", textContent: "Open draft ↗" }),
            el("span", { className: "muted", textContent: `To ${d.to} · ${ago(d.at)}` }))));
      }
    }
    return wrap;
  }

  // -------------------------------------------------------- profile form
  const lines = (v) => String(v || "").split(/\n|,/).map((s) => s.trim()).filter(Boolean); // "a, b" lists
  const rows = (v) => String(v || "").split("\n").map((s) => s.trim()).filter(Boolean);    // one per line

  const INDIAN_CITIES = ["Hyderabad", "Bengaluru", "Chennai", "Pune", "Mumbai", "Navi Mumbai", "Thane", "Delhi", "Gurugram",
    "Noida", "Faridabad", "Ghaziabad", "Kolkata", "Ahmedabad", "Gandhinagar", "Vadodara", "Surat", "Kochi", "Thiruvananthapuram",
    "Coimbatore", "Madurai", "Jaipur", "Chandigarh", "Mohali", "Indore", "Bhopal", "Bhubaneswar", "Nagpur", "Nashik", "Lucknow",
    "Mysuru", "Mangaluru", "Visakhapatnam", "Vijayawada", "Warangal", "Goa", "Kanpur", "Patna", "Secunderabad"];

  // Add / remove list with autocomplete (Indian cities) or free text
  function chipList(values, placeholder, suggest) {
    let items = [...(values || [])];
    const box = el("div", { className: "chips" });
    const draw = () => box.replaceChildren(...items.map((v) => {
      const b = el("button", { type: "button", className: "chip", title: "Remove" }, v, el("span", { className: "chip-count", textContent: "×" }));
      b.setAttribute("aria-pressed", "true");
      b.addEventListener("click", () => { items = items.filter((x) => x !== v); draw(); });
      return b;
    }));
    const input = el("input", { placeholder, enterKeyHint: "done" });
    if (suggest) {
      if (!document.getElementById("in-cities")) {
        document.body.append(el("datalist", { id: "in-cities" }, ...INDIAN_CITIES.map((c) => el("option", { value: c }))));
      }
      input.setAttribute("list", "in-cities");
    }
    const add = () => {
      for (const v of lines(input.value)) if (!items.some((x) => x.toLowerCase() === v.toLowerCase())) items.push(v);
      input.value = "";
      draw();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    input.addEventListener("change", () => suggest && INDIAN_CITIES.includes(input.value) && add());
    draw();
    return { node: el("div", { className: "chip-edit" }, box, input), get: () => (input.value.trim() && add(), items), set: (v) => { items = [...v]; draw(); } };
  }

  function profileForm(p, { onSaved, intro } = {}) {
    const form = el("form", { className: "pf" });
    const field = (label, input, hint) => el("label", { className: "lg-field" }, el("span", {}, label, hint ? el("small", { className: "muted", textContent: ` · ${hint}` }) : null), input);
    const text = (name, value, attrs = {}) => el("input", { name, value: value ?? "", ...attrs });
    const area = (name, value) => el("textarea", { name, rows: 3, value: (value || []).join("\n") });
    let skills = [...(p.skills || [])];
    const chips = el("div", { className: "chips" });
    const drawSkills = () => chips.replaceChildren(...skills.map((s) => {
      const b = el("button", { type: "button", className: "chip", title: "Remove" }, s, el("span", { className: "chip-count", textContent: "×" }));
      b.setAttribute("aria-pressed", "true");
      b.addEventListener("click", () => {
        skills = skills.filter((x) => x !== s);
        drawSkills();
      });
      return b;
    }));
    drawSkills();
    const addSkill = el("input", { placeholder: "Add a skill and press Enter", enterKeyHint: "done" });
    addSkill.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      for (const s of lines(addSkill.value)) if (!skills.some((x) => x.toLowerCase() === s.toLowerCase())) skills.push(s);
      addSkill.value = "";
      drawSkills();
    });
    const threshold = el("input", { name: "threshold", type: "range", min: 0, max: 100, step: 5, value: p.threshold ?? 70 });
    const tOut = el("output", { textContent: threshold.value });
    threshold.addEventListener("input", () => (tOut.textContent = threshold.value));
    const check = (name, on, label) => el("label", { className: "lg-check" }, el("input", { type: "checkbox", name, checked: on }), ` ${label}`);
    const radio = (name, value, on, label) => el("label", { className: "lg-check" }, el("input", { type: "radio", name, value, checked: on }), ` ${label}`);
    const cities = chipList(p.locations, "Type a city, e.g. Hyderabad", true);
    const scope = p.search_scope || (p.anywhere_in_india || !(p.locations || []).length ? "india" : "selected");
    const modes = p.work_modes || (p.remote_ok === false ? ["onsite", "hybrid"] : ["onsite", "hybrid", "remote"]);
    const relocation = el("select", { name: "relocation" }, ...[["flexible", "Flexible"], ["yes", "Willing to relocate"], ["no", "Not willing to relocate"]]
      .map(([v, l]) => el("option", { value: v, textContent: l, selected: (p.relocation || "flexible") === v })));
    const wCities = chipList(p.walkin_cities, "Type a city, e.g. Hyderabad", true);
    const wRoles = chipList(p.walkin_roles, "Add a role and press Enter");
    const wInd = chipList(p.walkin_industries, "e.g. Banking, IT services");
    const exclCos = chipList(p.excluded_companies, "Company name, then Enter");
    const copyCities = el("button", { type: "button", className: "link-btn", textContent: "Use my job cities for walk-ins" });
    copyCities.addEventListener("click", () => wCities.set(cities.get()));
    const wScore = el("input", { name: "walkin_min_score", type: "range", min: 0, max: 100, step: 5, value: p.walkin_min_score ?? 50 });
    const wOut = el("output", { textContent: wScore.value });
    wScore.addEventListener("input", () => (wOut.textContent = wScore.value));
    const reset = el("button", { type: "button", className: "link-btn", textContent: "Reset job and walk-in preferences" });
    reset.addEventListener("click", () => {
      cities.set([]); wCities.set([]); wRoles.set([]); wInd.set([]);
      for (const i of form.querySelectorAll("input[name=work_modes]")) i.checked = true;
      form.querySelector("input[name=search_scope][value=india]").checked = true;
      relocation.value = "flexible";
      for (const n of ["walkin_radius_km", "walkin_from", "walkin_to", "walkin_min_years", "walkin_max_years"]) form.elements[n].value = "";
      wScore.value = 50; wOut.textContent = "50";
      toast("Preferences reset. Save to keep the change.");
    });
    const err = el("p", { className: "lg-error", role: "alert" });
    const save = el("button", { className: "btn btn-primary", type: "submit", textContent: "Save profile" });
    form.append(...[
      intro ? el("p", { className: "note", textContent: intro }) : null,
      field("Full name", text("full_name", p.full_name, { required: true, autocomplete: "name" })),
      field("Email for applications", text("email", p.email || session?.email, { type: "email", required: true })),
      field("Phone", text("phone", p.phone, { type: "tel", autocomplete: "tel" }), "optional"),
      field("LinkedIn", text("linkedin", p.linkedin), "optional"),
      field("Current role", text("current_role", p.current_role)),
      field("Current company", text("current_company", p.current_company), "optional"),
      field("Years of experience", text("years_experience", p.years_experience, { type: "number", min: 0, max: 50, step: 0.5 })),
      field("Roles you want", text("target_roles", (p.target_roles || [p.current_role].filter(Boolean)).join(", ")), "comma separated"),
      el("div", { className: "lg-field" }, el("span", {}, "Your skills", el("small", { className: "muted", textContent: " · only these are used; tap to remove" })), chips, addSkill),
      el("h4", { className: "pf-h", textContent: "Job preferences" }),
      el("div", { className: "lg-field" }, el("span", {}, "Preferred job locations", el("small", { className: "muted", textContent: " · add several" })), cities.node),
      el("div", { className: "lg-field" }, el("span", {}, "Search"),
        radio("search_scope", "selected", scope === "selected", "Only my cities (and remote, if chosen)"),
        radio("search_scope", "india", scope === "india", "Anywhere in India")),
      el("div", { className: "lg-field" }, el("span", {}, "Work arrangement", el("small", { className: "muted", textContent: " · tick all that suit you" })),
        el("div", { className: "row" }, ...[["onsite", "On-site"], ["hybrid", "Hybrid"], ["remote", "Remote (anywhere in India)"]]
          .map(([v, l]) => el("label", { className: "lg-check" }, el("input", { type: "checkbox", name: "work_modes", value: v, checked: modes.includes(v) }), ` ${l}`)))),
      field("Relocation", relocation),
      field("Experience range to search (years)", el("div", { className: "row" },
        text("min_years", p.min_years, { type: "number", min: 0, max: 50, placeholder: "min" }),
        text("max_years", p.max_years, { type: "number", min: 0, max: 50, placeholder: "max" })), "optional"),
      field("Notice period", text("notice", p.notice, { placeholder: "e.g. Immediate joiner, 30 days" })),
      field("Current CTC", text("current_ctc", p.current_ctc, { placeholder: "e.g. 8 LPA" }), "optional, for form answers"),
      field("Expected CTC", text("expected_ctc", p.expected_ctc, { placeholder: "e.g. 12 LPA" }), "optional, for form answers"),
      field("Portfolio / GitHub", text("portfolio", p.portfolio, { type: "url", placeholder: "https://" }), "optional"),
      field("Education", area("education", p.education), "one per line"),
      field("Certifications", area("certifications", p.certifications), "one per line"),
      field("Highlights for drafts", area("highlights", p.highlights), "up to 3 lines, your own words"),
      el("label", { className: "lg-field" }, el("span", {}, "Show jobs scoring at least ", tOut), threshold),
      el("h4", { className: "pf-h", textContent: "Walk-in interviews" }),
      el("p", { className: "muted", textContent: "You have to be there in person, so walk-ins use their own cities. Leave empty to see none." }),
      el("div", { className: "lg-field" }, el("span", {}, "Walk-in cities"), wCities.node),
      copyCities,
      field("Max travel from those cities (km)", text("walkin_radius_km", p.walkin_radius_km, { type: "number", min: 0, max: 1000, placeholder: "e.g. 30" }), "optional"),
      field("Dates", el("div", { className: "row" }, text("walkin_from", p.walkin_from, { type: "date" }), text("walkin_to", p.walkin_to, { type: "date" })), "optional"),
      el("div", { className: "lg-field" }, el("span", {}, "Walk-in roles", el("small", { className: "muted", textContent: " · empty = your roles above" })), wRoles.node),
      el("div", { className: "lg-field" }, el("span", {}, "Industries", el("small", { className: "muted", textContent: " · optional, matched in the listing text" })), wInd.node),
      field("Walk-in experience (years)", el("div", { className: "row" },
        text("walkin_min_years", p.walkin_min_years, { type: "number", min: 0, max: 50, placeholder: "min" }),
        text("walkin_max_years", p.walkin_max_years, { type: "number", min: 0, max: 50, placeholder: "max" })), "optional"),
      el("label", { className: "lg-field" }, el("span", {}, "Walk-ins scoring at least ", wOut), wScore),
      reset,
      check("drafts_enabled", p.drafts_enabled ?? true, "Allow Gmail drafts (never sent automatically)"),
      el("h4", { className: "pf-h", textContent: "Morning recruiter drafts" }),
      el("p", { className: "muted", textContent: "Every day at 9:00 AM, Job Radar looks at your matches for recruiters who wrote their email in the job post, and creates a draft to each one in your Gmail with your resume attached. You review and send them yourself. It also notices when you've sent one or got a reply, and drafts a follow-up in the same thread after 5 working days. Needs Gmail connected." }),
      check("auto_drafts", p.auto_drafts ?? false, "Create recruiter drafts every morning"),
      field("Most drafts per morning", text("auto_draft_limit", p.auto_draft_limit ?? 5, { type: "number", min: 1, max: 15 }), "1–15"),
      el("div", { className: "lg-field" }, el("span", {}, "Never draft to these companies", el("small", { className: "muted", textContent: " · e.g. your current employer" })), exclCos.node),
      check("notify", p.notify ?? true, "Notify me about new matches"),
      err, el("div", { className: "row" }, save)].filter(Boolean));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      const f = new FormData(form);
      const num = (k) => (f.get(k) === "" ? null : Number(f.get(k)));
      const body = {
        full_name: f.get("full_name"), email: f.get("email"), phone: f.get("phone"), linkedin: f.get("linkedin"),
        current_role: f.get("current_role"), current_company: f.get("current_company"), years_experience: num("years_experience"),
        target_roles: lines(f.get("target_roles")), skills, locations: cities.get(),
        search_scope: f.get("search_scope") || "india", relocation: f.get("relocation") || "flexible",
        work_modes: f.getAll("work_modes"),
        walkin_cities: wCities.get(), walkin_roles: wRoles.get(), walkin_industries: wInd.get(),
        walkin_radius_km: num("walkin_radius_km"), walkin_from: f.get("walkin_from") || null, walkin_to: f.get("walkin_to") || null,
        walkin_min_years: num("walkin_min_years"), walkin_max_years: num("walkin_max_years"), walkin_min_score: Number(wScore.value),
        min_years: num("min_years"), max_years: num("max_years"), notice: f.get("notice"),
        current_ctc: f.get("current_ctc"), expected_ctc: f.get("expected_ctc"), portfolio: f.get("portfolio"),
        added_skills: (p.added_skills || []).filter((k) => skills.some((x) => x.toLowerCase() === k.toLowerCase())),
        hidden_skills: p.hidden_skills || [],
        auto_drafts: f.get("auto_drafts") === "on", auto_draft_limit: num("auto_draft_limit") ?? 5, excluded_companies: exclCos.get(),
        education: rows(f.get("education")), certifications: rows(f.get("certifications")),
        highlights: rows(f.get("highlights")).slice(0, 3),
        degree_levels: p.degree_levels || [], projects: p.projects || [],
        threshold: Number(threshold.value), drafts_enabled: f.get("drafts_enabled") === "on", notify: f.get("notify") === "on",
      };
      if (!isVerified() && !(await checkVerification())) {
        err.textContent = "Verify your email first: open the link we emailed you, then tap \u201cI've verified my email — Check again\u201d.";
        return;
      }
      await withBusy(save, "Saving…", async () => {
        try {
          const saved = await api("/api/profile", { method: "PUT", body });
          await refreshMe();
          renderBanner();
          toast(saved.searching ? "Profile saved. Searching for jobs that fit you: usually a few minutes, they'll appear on their own."
            : `Profile saved · ${saved.count ?? 0} matches at ${body.threshold}+`);
          onSaved?.();
          load({ quiet: true });
        } catch (ex) {
          err.textContent = ex.message;
        }
      });
    });
    return form;
  }

  function resumeBlock(onSuggested) {
    const file = me?.resume;
    const input = el("input", { type: "file", accept: ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document", hidden: true });
    const pick = el("button", { className: "btn btn-primary", type: "button", textContent: file ? "Replace resume" : "Upload resume (PDF or Word)" });
    pick.addEventListener("click", () => input.click());
    const msg = el("p", { className: "muted", role: "status" });
    if (!isVerified()) {
      pick.disabled = true;
      msg.textContent = "Verify your email to upload a resume.";
    }
    input.addEventListener("change", async () => {
      const f = input.files[0];
      input.value = "";
      if (!f) return;
      if (f.size > 2 * 1024 * 1024) return (msg.textContent = "That file is over 2 MB. Export a smaller PDF.");
      msg.textContent = "Reading your resume…";
      try {
        const out = await api("/api/resume", { method: "POST", raw: f, headers: { "X-Filename": encodeURIComponent(f.name), "Content-Type": "application/octet-stream" } });
        await refreshMe();
        msg.textContent = "Done. Check the details below, fix anything that's wrong, then save.";
        onSuggested?.(out.suggested);
      } catch (e) {
        msg.textContent = e.message;
      }
    });
    const del = file ? el("button", { className: "btn", type: "button", textContent: "Delete resume" }) : null;
    del?.addEventListener("click", async () => {
      if (!confirm("Delete your stored resume? Drafts will no longer attach it. Your confirmed profile stays.")) return;
      try {
        await api("/api/resume", { method: "DELETE" });
        await refreshMe();
        toast("Resume deleted");
        renderSettings();
      } catch (e) {
        toast(e.message);
      }
    });
    return el("div", {},
      el("p", { className: "muted", textContent: file ? `${file.name} · uploaded ${ago(file.uploaded)} · stored encrypted, only you and your drafts use it.` : "Your resume is stored encrypted and is only attached to drafts you create." }),
      el("div", { className: "row" }, pick, del), input, msg);
  }

  function mergeSuggested(p, s) {
    const out = { ...p };
    for (const k of ["full_name", "email", "phone", "linkedin", "current_role", "years_experience"]) if (!out[k] && s[k]) out[k] = s[k];
    for (const k of ["education", "certifications", "projects", "highlights", "degree_levels"]) if (!(out[k] || []).length && s[k]?.length) out[k] = s[k];
    out.skills = [...new Set([...(out.skills || []), ...(s.skills || [])])];
    if (!(out.target_roles || []).length && s.current_role) out.target_roles = [s.current_role];
    return out;
  }

  function openOnboarding() {
    const dlg = $("#onboard");
    const body = $("#onboard-body");
    let draft = { ...profile() };
    const formHolder = el("div");
    const drawForm = (intro) => formHolder.replaceChildren(profileForm(draft, { intro, onSaved: () => closeOverlay(dlg) }));
    body.replaceChildren(...[
      isVerified() ? null : verifyBox(),
      el("p", { className: "muted", textContent: "Upload your resume and Job Radar suggests your details. Nothing is used until you confirm it. You can also fill it in by hand." }),
      resumeBlock((s) => {
        draft = mergeSuggested(draft, s);
        drawForm(s.years_source === "dates" ? "Years of experience were estimated from the dates in your resume. Check them." : "Check every field: suggestions can be wrong.");
      }),
      formHolder].filter(Boolean));
    drawForm();
    if (!dlg.open) openOverlay(dlg);
  }

  // ------------------------------------------------------------- walk-ins
  let walk = null, walkAt = 0;
  const wf = { city: [], verifiedOnly: false, minScore: 0, q: "", when: "all" };

  async function loadWalkins(force = false) {
    if (!force && walk && Date.now() - walkAt < 120e3) return walk;
    walk = await api("/api/walkins");
    walkAt = Date.now();
    return walk;
  }

  function icsFor(ev) {
    const d = ev.dates[0].replace(/-/g, "");
    const esc = (t) => String(t || "").replace(/[\\,;]/g, (m) => "\\" + m).replace(/\n/g, " ");
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Job Radar//Walk-ins//EN", "BEGIN:VEVENT",
      `UID:${ev.id}@jobradar`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`,
      `DTSTART;VALUE=DATE:${d}`, `SUMMARY:${esc(`Walk-in: ${ev.title} · ${ev.company}`)}`,
      `LOCATION:${esc(ev.venue || ev.city)}`,
      `DESCRIPTION:${esc([ev.time && `Time: ${ev.time}`, ev.documents && `Bring: ${ev.documents}`, `Source: ${ev.url}`].filter(Boolean).join(" | "))}`,
      "BEGIN:VALARM", "TRIGGER:-PT12H", "ACTION:DISPLAY", "DESCRIPTION:Walk-in tomorrow", "END:VALARM",
      "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  }

  function walkCard(ev) {
    const saved = store.walkins[ev.id]?.saved;
    const save = el("button", { className: `btn${saved ? " is-applied" : ""}`, type: "button", textContent: saved ? "✓ Saved" : "Save walk-in" });
    save.addEventListener("click", () => {
      store.walkins[ev.id] = { saved: !saved, at: new Date().toISOString(), event: saved ? undefined : ev };
      changed();
      render();
    });
    const actions = el("div", { className: "row" }, save,
      el("a", { className: "btn", href: safeUrl(ev.url), target: "_blank", rel: "noopener noreferrer", textContent: "View source ↗" }));
    if (ev.status === "confirmed") {
      const cal = el("button", { className: "btn", type: "button", textContent: "Add to calendar" });
      cal.addEventListener("click", () => download(`walk-in-${ev.company}.ics`.replace(/[^\w.-]+/g, "-"), new Blob([icsFor(ev)], { type: "text/calendar" })));
      actions.append(cal);
    }
    const when = ev.dates.length ? ev.dates.map((d) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })).join(", ") : "Date not published";
    const line = (label, v) => (v ? el("p", { className: "muted" }, el("b", { textContent: `${label}: ` }), v) : null);
    const exp = ev.experience.min != null ? `${ev.experience.min}${ev.experience.max ? "–" + ev.experience.max : "+"} years` : null;
    return el("article", { className: "panel-item walkin" }, ...[
      el("div", { className: "panel-top" }, el("b", { textContent: ev.company }),
        el("span", { className: `st-chip ${ev.status === "confirmed" ? "st-applied" : "st-pending"}`,
          textContent: ev.status === "confirmed" ? "date & venue in posting" : `unconfirmed: no ${ev.missing.join(" or ")}` })),
      el("p", {}, el("b", { textContent: ev.title }), ` · match ${ev.score}`),
      el("p", { textContent: `📅 ${when}${ev.time ? " · " + ev.time : ""}` }),
      el("p", { textContent: `📍 ${ev.venue || ev.location || ev.city}${ev.distance_km ? ` (${ev.distance_km} km from your city)` : ""}` }),
      line("Experience", exp), line("Pay", ev.salary), line("Bring", ev.documents), line("Registration", ev.registration),
      el("p", { className: "muted", textContent: `From ${SOURCE_LABELS[ev.source] || ev.source}${ev.posted ? " · posted " + ago(ev.posted) : ""} · checked ${ago(walk.checked)}. Confirm the details on the source before going.` }),
      actions].filter(Boolean));
  }

  function renderWalkins() {
    const wrap = el("div", { className: "panel-list" });
    const editBtn = el("button", { className: "btn", type: "button", textContent: "Walk-in preferences" });
    editBtn.addEventListener("click", openOnboarding);
    if (!walk) {
      wrap.append(el("p", { className: "muted", textContent: "Looking for walk-ins…" }));
      loadWalkins().then(() => section === "walkins" && render()).catch((e) => toast(e.message));
      return wrap;
    }
    if (!walk.cities.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "Choose your walk-in cities" }),
        "Walk-ins need you there in person, so they use their own cities. Add them in your profile."), el("div", { className: "row" }, editBtn));
      return wrap;
    }
    const all = [...walk.upcoming, ...walk.unconfirmed];
    const cities = [...new Set(all.map((e) => e.city))].sort();
    const today = new Date().toISOString().slice(0, 10);
    const week = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
    const shown = all.filter((e) => (!wf.city.length || wf.city.includes(e.city)) && (!wf.verifiedOnly || e.status === "confirmed")
      && e.score >= wf.minScore && (!wf.q || `${e.title} ${e.company}`.toLowerCase().includes(wf.q.toLowerCase()))
      && (wf.when === "all" || (e.dates.length && e.dates.some((d) => d >= today && d <= week))));
    const chip = (label, on, run) => { const b = el("button", { type: "button", className: "chip" }, label); b.setAttribute("aria-pressed", String(on)); b.addEventListener("click", run); return b; };
    const search = el("input", { type: "search", placeholder: "Role or company", value: wf.q });
    search.addEventListener("change", () => { wf.q = search.value.trim(); render(); });
    const score = el("input", { type: "range", min: 0, max: 100, step: 5, value: wf.minScore });
    score.addEventListener("change", () => { wf.minScore = Number(score.value); render(); });
    wrap.append(el("div", { className: "panel-item" },
      el("p", { className: "muted", textContent: `Walk-ins in ${walk.cities.join(", ")}. Only postings that say they're walk-ins; details come from the posting.` }),
      el("div", { className: "chips" }, chip("All cities", !wf.city.length, () => { wf.city = []; render(); }),
        ...cities.map((c) => chip(c, wf.city.includes(c), () => { wf.city = wf.city.includes(c) ? wf.city.filter((x) => x !== c) : [...wf.city, c]; render(); }))),
      el("div", { className: "chips" }, chip("Any date", wf.when === "all", () => { wf.when = "all"; render(); }),
        chip("Next 7 days", wf.when === "week", () => { wf.when = "week"; render(); }),
        chip("Verified upcoming only", wf.verifiedOnly, () => { wf.verifiedOnly = !wf.verifiedOnly; render(); })),
      el("label", { className: "lg-field" }, el("span", {}, `Match at least ${wf.minScore}`), score), search,
      el("div", { className: "row" }, editBtn)));
    if (!shown.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: all.length ? "No walk-ins match these filters" : `No walk-ins found in ${walk.cities.join(", ")}` }),
        all.length ? "Try clearing a filter." : "None of the current job sources has a walk-in for your cities right now. Job Radar checks every 3 hours." +
          (walk.expired ? ` ${walk.expired} past walk-in(s) were hidden.` : "")));
      return wrap;
    }
    for (const ev of shown) wrap.append(walkCard(ev));
    if (walk.expired) wrap.append(el("p", { className: "muted", textContent: `${walk.expired} past walk-in(s) hidden.` }));
    return wrap;
  }

  // ------------------------------------------------------------ settings
  function renderSettings() {
    const body = $("#account-body");
    const sec = $("#account-section");
    if (!enabled()) return (sec.hidden = true);
    sec.hidden = false;
    if (!me) {
      body.replaceChildren(el("p", { className: "muted", textContent: "Loading your account…" }));
      refreshMe().then(renderSettings).catch((e) => body.replaceChildren(el("p", { className: "lg-error", textContent: e.message })));
      return;
    }
    const p = profile();
    const edit = el("button", { className: "btn", type: "button", textContent: "Edit profile & preferences" });
    edit.addEventListener("click", () => {
      closeOverlay($("#settings"));
      openOnboarding();
    });
    const del = el("button", { className: "btn danger", type: "button", textContent: "Delete my account" });
    del.addEventListener("click", deleteAccount);
    body.replaceChildren(
      el("h4", { textContent: "Email" }), verifyBox(),
      el("h4", { textContent: "Profile" }),
      el("p", { className: "muted", textContent: p.full_name
        ? `${p.full_name} · ${p.current_role || "role not set"} · ${p.years_experience ?? "?"} yrs · showing jobs scoring ${p.threshold ?? 70}+ · ${(p.skills || []).length} skills`
        : "Not set up yet." }),
      el("div", { className: "row" }, edit),
      el("h4", { textContent: "Resume" }), resumeBlock(() => openOnboarding()),
      el("h4", { textContent: "Gmail" }), gmailCard(),
      el("h4", { textContent: "Account" }),
      el("p", { className: "muted", textContent: "Deleting removes your profile, resume, matches, statuses, Gmail connection (revoked at Google) and your sign-in. This can't be undone." }),
      el("div", { className: "row" }, del));
  }

  async function deleteAccount() {
    const typed = prompt("This permanently deletes your Job Radar account and data. Type DELETE to confirm.");
    if (typed !== "DELETE") return;
    try {
      await api("/api/account", { method: "DELETE" });
    } catch (e) {
      return toast(e.message);
    }
    writeJSON(K.dirty, undefined);
    store = JRSync.empty();
    saveLocal();
    me = null;
    closeOverlay($("#settings"));
    toast("Your account was deleted.");
    try {
      await JRCloud.signOut();
    } catch {
      onUserChanged(null);
    }
  }

  // ---------------------------------------------------------------- push
  async function pushKey() {
    const r = await fetch(apiBase() + "/api/push/key").catch(() => null);
    return r && r.ok ? (await r.json()).publicKey : null;
  }
  async function savePush(sub) {
    await api("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON() } });
  }

  function signedOut() {
    unwatch.forEach((u) => u());
    unwatch = [];
    me = null;
    matchesCache = null;
    $("#acct-banner").hidden = true;
  }

  const mailCount = () => recruiterEntries().filter((e) => !["closed", "replied"].includes(e.stage)).length;
  const inboxOpen = () => (store.gmail.events || []).filter((ev) => !store.done[ev.id]?.on).length +
    (store.gmail.review || []).filter((r) => !store.reviews[r.id]).length;

  return { enabled, api, refreshMe, mailCount, inboxOpen, onSignedIn, loadMatches, decorateCard, renderInbox, renderDrafts, renderSettings,
    openOnboarding, pushKey, savePush, signedOut, profile, handleReturn, checkVerification, refreshNow, renderWalkins,
    refreshWalkins: () => loadWalkins(true).then(() => section === "walkins" && render()).catch((e) => toast(e.message)), get me() { return me; } };
})();
