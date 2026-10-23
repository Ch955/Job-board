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

  async function api(path, { method = "GET", body, raw, headers = {} } = {}) {
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
    renderBanner();
    handleReturn();
    if (me.email_verified && me.missing?.length) openOnboarding();
    load({ quiet: true });
  }

  function renderBanner() {
    const bar = $("#acct-banner");
    if (!enabled() || !me) return (bar.hidden = true);
    if (!me.email_verified) {
      const resend = el("button", { className: "link-btn", type: "button", textContent: "Send the link again" });
      resend.addEventListener("click", async () => {
        try {
          await JRCloud.sendVerification();
          toast("Verification email sent. Check your inbox (and spam).");
        } catch (e) {
          toast(authMessage(e) || "Couldn't send it. Try again in a minute.");
        }
      });
      const done = el("button", { className: "link-btn", type: "button", textContent: "I've verified" });
      done.addEventListener("click", async () => {
        await JRCloud.reloadUser();
        await refreshMe().catch(() => null);
        renderBanner();
        if (me?.email_verified && me.missing?.length) openOnboarding();
      });
      bar.replaceChildren(el("span", { textContent: `Verify ${me.email} to finish setting up: open the link we emailed you.` }), resend, done);
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
  async function loadMatches() {
    const m = await api("/api/matches");
    matchesCache = m;
    const p = profile();
    return {
      jobs: m.jobs || [], generated_at: m.updated, alert_score: p.threshold ?? 70,
      outreach: { profile: { name: (p.full_name || "").split(" ")[0], full_name: p.full_name, role: p.current_role,
        years_experience: p.years_experience, notice: p.notice, linkedin: p.linkedin }, my_skills: p.skills || [] },
    };
  }

  function decorateCard(job, card) {
    if (!enabled()) return;
    const m = job.match;
    if (m) {
      const why = el("div", { className: "why" }, el("p", { textContent: m.summary }));
      if (m.missing_skills?.length) {
        why.append(el("ul", { className: "keywords missing" }, ...m.missing_skills.slice(0, 6).map((k) => el("li", { textContent: k }))));
      }
      card.querySelector(".snippet").before(why);
    }
    const status = statusOf(job);
    if (!draftsOn() || job.source === "email" || !["new", "later", "applied", "interview"].includes(status)) return;
    const row = card.querySelector(".outreach-row");
    const kinds = status === "new" || status === "later" ? [["application", "✉️ Draft application"], ["outreach", "✉️ Draft outreach"]]
      : [["follow_up", "✉️ Draft follow-up"]];
    for (const [kind, label] of kinds) {
      const rec = store.drafts[`${kind}:${job.id}`];
      if (rec) {
        row.append(el("a", { className: "mini-btn mail", href: "https://mail.google.com/mail/u/0/#drafts", target: "_blank",
          rel: "noopener", textContent: `✉️ ${DRAFT_LABEL[kind]} draft ready` }));
        continue;
      }
      const b = el("button", { className: "mini-btn", type: "button", textContent: label });
      b.addEventListener("click", () => draftForJob(job, kind, b));
      row.append(b);
    }
  }

  async function draftForJob(job, kind, button) {
    const guess = (job.contact_emails || [])[0] || "";
    const to = prompt(`Recruiter's email address for ${job.company}:`, guess);
    if (to === null) return;
    if (!/^[^@\s<>]+@[^@\s<>]+\.[a-z]{2,24}$/i.test(to.trim())) return toast("That email address doesn't look right.");
    await createDraft({ type: kind, job_id: job.id, to: to.trim() }, button);
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
      if (!quiet) toast(`Gmail checked · ${r.report.updated || 0} updated, ${r.report.review || 0} to review`);
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
    const events = store.gmail.events || [];
    wrap.append(el("h3", { className: "panel-h", textContent: "Recent job emails" }));
    if (!events.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No job emails yet" }),
        "Confirmations, tests, interview invites, rejections, offers and recruiter replies appear here."));
      return wrap;
    }
    for (const ev of events.slice(0, 60)) {
      const row = el("div", { className: "row" });
      if (ev.job_id) {
        const show = el("button", { className: "btn", type: "button", textContent: "Show job" });
        show.addEventListener("click", () => focusJob(ev.job_id));
        row.append(show);
      }
      if (draftsOn()) {
        for (const [kind, label] of REPLY_DRAFTS[ev.type] || []) {
          const rec = store.drafts[`${kind}:${ev.thread_id}`];
          if (rec) {
            row.append(el("a", { className: "btn", href: "https://mail.google.com/mail/u/0/#drafts", target: "_blank", rel: "noopener", textContent: `${DRAFT_LABEL[kind]} draft ready ↗` }));
          } else {
            const b = el("button", { className: "btn", type: "button", textContent: label });
            b.addEventListener("click", () => createDraft({ type: kind, message_id: ev.id, job_id: ev.job_id || undefined }, b));
            row.append(b);
          }
        }
      }
      const chip = ev.applied ? `st-${ev.status}` : ev.needs_reply ? "st-interview" : "st-rejected";
      wrap.append(el("article", { className: "panel-item" },
        el("div", { className: "panel-top" }, el("b", { textContent: ev.company || ev.from }),
          el("span", { className: `st-chip ${chip}`, textContent: TYPE_LABEL[ev.type] || ev.type })),
        el("p", { textContent: ev.title || ev.subject }),
        el("p", { className: "muted", textContent: `${fmtDate(ev.at)} · ${ev.subject}${ev.applied ? " · status updated" : ev.needs_reply ? " · may need a reply" : ""}` }),
        row.children.length ? row : null));
    }
    return wrap;
  }

  function renderDrafts() {
    const wrap = el("div", { className: "panel-list" });
    const recs = Object.entries(store.drafts || {}).sort((a, b) => String(b[1].at).localeCompare(String(a[1].at)));
    wrap.append(el("p", { className: "muted", textContent: draftsOn() ? "Drafts are created in your Gmail and never sent by Job Radar. Review and send them yourself."
      : me?.gmail?.connected ? "Drafts are turned off in Settings." : "Connect Gmail in Settings to create drafts." }),
    el("div", { className: "row" }, el("a", { className: "btn", href: "https://mail.google.com/mail/u/0/#drafts", target: "_blank", rel: "noopener", textContent: "All Gmail drafts ↗" })));
    if (!recs.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No drafts yet" }), "Use ✉️ on a job, or a reply button under Inbox updates."));
      return wrap;
    }
    for (const [, d] of recs) {
      const job = d.job_id && allJobs().find((j) => j.id === d.job_id);
      wrap.append(el("article", { className: "panel-item" },
        el("div", { className: "panel-top" }, el("b", { textContent: job?.company || d.to }), el("span", { className: "st-chip st-interview", textContent: DRAFT_LABEL[d.type] || d.type })),
        el("p", { textContent: d.subject }),
        el("p", { className: "muted", textContent: `To ${d.to} · ${ago(d.at)}${d.attached ? " · resume attached" : ""}` })));
    }
    return wrap;
  }

  // -------------------------------------------------------- profile form
  const lines = (v) => String(v || "").split(/\n|,/).map((s) => s.trim()).filter(Boolean); // "a, b" lists
  const rows = (v) => String(v || "").split("\n").map((s) => s.trim()).filter(Boolean);    // one per line

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
      field("Preferred cities", text("locations", (p.locations || []).join(", ")), "comma separated"),
      check("anywhere_in_india", p.anywhere_in_india ?? false, "Open to anywhere in India"),
      check("remote_ok", p.remote_ok ?? true, "Open to remote"),
      field("Experience range to search (years)", el("div", { className: "row" },
        text("min_years", p.min_years, { type: "number", min: 0, max: 50, placeholder: "min" }),
        text("max_years", p.max_years, { type: "number", min: 0, max: 50, placeholder: "max" })), "optional"),
      field("Notice period", text("notice", p.notice, { placeholder: "e.g. Immediate joiner, 30 days" })),
      field("Education", area("education", p.education), "one per line"),
      field("Certifications", area("certifications", p.certifications), "one per line"),
      field("Highlights for drafts", area("highlights", p.highlights), "up to 3 lines, your own words"),
      el("label", { className: "lg-field" }, el("span", {}, "Show jobs scoring at least ", tOut), threshold),
      check("drafts_enabled", p.drafts_enabled ?? true, "Allow Gmail drafts (never sent automatically)"),
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
        target_roles: lines(f.get("target_roles")), skills, locations: lines(f.get("locations")),
        anywhere_in_india: f.get("anywhere_in_india") === "on", remote_ok: f.get("remote_ok") === "on",
        min_years: num("min_years"), max_years: num("max_years"), notice: f.get("notice"),
        education: rows(f.get("education")), certifications: rows(f.get("certifications")),
        highlights: rows(f.get("highlights")).slice(0, 3),
        degree_levels: p.degree_levels || [], projects: p.projects || [],
        threshold: Number(threshold.value), drafts_enabled: f.get("drafts_enabled") === "on", notify: f.get("notify") === "on",
      };
      await withBusy(save, "Saving…", async () => {
        try {
          await api("/api/profile", { method: "PUT", body });
          await refreshMe();
          renderBanner();
          toast("Profile saved. Matching jobs for you…");
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
    body.replaceChildren(
      el("p", { className: "muted", textContent: "Upload your resume and Job Radar suggests your details. Nothing is used until you confirm it. You can also fill it in by hand." }),
      resumeBlock((s) => {
        draft = mergeSuggested(draft, s);
        drawForm(s.years_source === "dates" ? "Years of experience were estimated from the dates in your resume. Check them." : "Check every field: suggestions can be wrong.");
      }),
      formHolder);
    drawForm();
    if (!dlg.open) openOverlay(dlg);
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
    me = null;
    matchesCache = null;
    $("#acct-banner").hidden = true;
  }

  return { enabled, api, refreshMe, onSignedIn, loadMatches, decorateCard, renderInbox, renderDrafts, renderSettings,
    openOnboarding, pushKey, savePush, signedOut, profile, get me() { return me; } };
})();
