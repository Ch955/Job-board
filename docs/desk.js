/* Job Radar desk: the Apply-now toolbar (phone-friendly filter, quick answers,
   missing profile answers), Apply-later reasons, fit lines on cards, the
   Skill match page and the Pipeline table.

   Plain script, loaded after account.js and before app.js; like account.js it
   uses app.js helpers ($, el, toast, data, store...) only inside functions. */
"use strict";

const JRDesk = (() => {
  // ------------------------------------------------------- folded panels
  const collapsed = () => readJSON(K.collapsed, {});
  const isCollapsed = (key, dflt = false) => collapsed()[key] ?? dflt;
  const setCollapsed = (key, on) => writeJSON(K.collapsed, { ...collapsed(), [key]: !!on });

  const companyKey = (name) => String(name || "").toLowerCase()
    .replace(/\b(private|pvt|limited|ltd|inc|llp|technologies|technology|solutions|services|india)\b\.?/g, "").replace(/[^a-z0-9]/g, "");

  // ------------------------------------------------- phone or laptop?
  // From the evidence we have: how you apply (type), where (source), and whether the
  // listing says files must be uploaded. A guess, labelled as such on the badge.
  const UPLOAD = /upload (your |an updated )?(resume|cv)|attach (your )?(resume|cv)|cover letter (is )?(required|mandatory)|portfolio (is )?required/i;
  const PHONE_BOARDS = ["linkedin", "naukri", "indeed"];
  const needsFiles = (job) => !!job.match?.files_required || UPLOAD.test(`${job.description_snippet || ""}`);
  function deviceOf(job) {
    if (needsFiles(job)) return "laptop";
    const t = applyTypeOf(job);
    if (t === "easy_apply") return "phone";
    if (t === "company_site" || t === "external") return "laptop";
    return PHONE_BOARDS.includes(job.source) ? "phone" : "laptop";
  }
  function deviceWhy(job, status) {
    if (status === "later") return "Parked jobs need the laptop (account, form or files)";
    if (needsFiles(job)) return "The listing asks you to upload files";
    const t = applyTypeOf(job);
    if (t === "easy_apply") return "Quick apply on the platform";
    if (t === "company_site") return "Employer's careers site: usually an account and a resume upload";
    if (t === "external") return "Opens another site's form";
    return PHONE_BOARDS.includes(job.source) ? `${SOURCE_LABELS[job.source]}'s app handles applying` : "Apply method unknown: assume a laptop";
  }

  // ------------------------------------------------------ form answers
  function me() {
    if (JRAccount.enabled()) return { ...JRAccount.profile(), email: JRAccount.profile().email || session?.email };
    const p = data.outreach?.profile || {};
    return { full_name: p.full_name || p.name, current_role: p.role, years_experience: p.years_experience, notice: p.notice,
      linkedin: p.linkedin, email: session?.email };
  }
  const ANSWERS = [
    ["Full name", (p) => p.full_name],
    ["Email", (p) => p.email],
    ["Phone", (p) => p.phone],
    ["LinkedIn", (p) => p.linkedin],
    ["Current role", (p) => p.current_role],
    ["Current company", (p) => p.current_company],
    ["Total experience", (p) => (p.years_experience != null && p.years_experience !== "" ? `${p.years_experience} years` : "")],
    ["Notice period", (p) => p.notice],
    ["Current CTC", (p) => p.current_ctc],
    ["Expected CTC", (p) => p.expected_ctc],
    ["Current location", (p) => (p.locations || [])[0] || p.location],
    ["Highest education", (p) => (p.education || [])[0]],
    ["Portfolio / GitHub", (p) => p.portfolio],
  ];
  const OPTIONAL = ["Portfolio / GitHub", "Current company"];
  const answers = () => ANSWERS.map(([label, get]) => [label, String(get(me()) ?? "").trim()]);
  const missingAnswers = () => answers().filter(([label, v]) => !v && !OPTIONAL.includes(label)).map(([label]) => label);

  function copyAnswers(job) {
    const lines = answers().filter(([, v]) => v).map(([l, v]) => `${l}: ${v}`);
    if (job) {
      lines.unshift(`Applying for: ${job.title} at ${job.company}`);
      const skills = (job.matched_keywords || []).slice(0, 6);
      if (skills.length) lines.push(`Relevant skills: ${skills.join(", ")}`);
    }
    copyText(lines.join("\n"), job ? `Answers for ${job.company} copied` : "Answers copied");
  }

  function quickAnswers() {
    const folded = isCollapsed("answers", false);
    const have = answers().filter(([, v]) => v);
    const head = el("button", { type: "button", className: "qa-head", textContent: `${folded ? "▸" : "▾"} Quick answers · tap to copy` });
    head.dataset.safe = "1";
    head.setAttribute("aria-expanded", String(!folded));
    head.addEventListener("click", () => {
      setCollapsed("answers", !folded);
      render();
    });
    const chips = el("div", { className: "qa-chips" }, ...have.map(([label, v]) => {
      const b = el("button", { type: "button", className: "chip qa" }, el("small", { textContent: label }), v);
      b.dataset.safe = "1";
      b.addEventListener("click", () => copyText(v, `${label} copied`));
      return b;
    }));
    const all = el("button", { type: "button", className: "chip qa all", textContent: "📋 Copy all" });
    all.dataset.safe = "1";
    all.addEventListener("click", () => copyAnswers(null));
    chips.append(all);
    return el("div", { className: "qa-bar" }, head, folded ? null : have.length ? chips
      : el("p", { className: "muted", textContent: "Add your details to your profile and they show here, one tap to copy." }));
  }

  function missingInfoCard() {
    const miss = missingAnswers();
    if (!miss.length || isCollapsed("missingInfo", false)) return null;
    const fix = el("button", { className: "btn", type: "button", textContent: "Add to profile" });
    fix.dataset.safe = "1";
    fix.addEventListener("click", () => (JRAccount.enabled() ? JRAccount.openOnboarding() : openSettings()));
    const hide = el("button", { className: "link-btn", type: "button", textContent: "Hide" });
    hide.dataset.safe = "1";
    hide.addEventListener("click", () => {
      setCollapsed("missingInfo", true);
      render();
      toast("Hidden. It comes back if you clear site data.");
    });
    return el("div", { className: "note missing-info" },
      el("b", { textContent: "Missing profile information" }),
      el("p", { textContent: `Application forms usually ask these, and your profile can't answer them yet: ${miss.join(", ")}.` }),
      el("div", { className: "row" }, fix, hide));
  }

  function applyBar(phoneCount, total) {
    const on = readJSON(K.phoneOnly, false);
    const phone = el("button", { type: "button", className: "chip", textContent: `📱 Phone only · ${phoneCount} of ${total}` });
    phone.dataset.safe = "1";
    phone.setAttribute("aria-pressed", String(on));
    phone.addEventListener("click", () => {
      writeJSON(K.phoneOnly, !on);
      render();
      toast(on ? "Showing every job" : "Showing jobs you can apply to from the phone");
    });
    const mode = readJSON(K.sortNew, "best");
    const sort = el("span", { className: "seg" }, ...[["best", "Best order"], ["newest", "Newest found"]].map(([v, l]) => {
      const b = el("button", { type: "button", className: "seg-btn", textContent: l });
      b.dataset.safe = "1";
      b.setAttribute("aria-pressed", String(mode === v));
      b.title = v === "best" ? "Jobs you opened, then quick applies, then the rest; each by fit" : "Newest found first, then by fit";
      b.addEventListener("click", () => {
        writeJSON(K.sortNew, v);
        render();
      });
      return b;
    }));
    sort.setAttribute("role", "group");
    sort.setAttribute("aria-label", "Order");
    return [el("div", { className: "apply-tools" }, phone, sort), quickAnswers(), missingInfoCard()].filter(Boolean);
  }

  // ------------------------------------------------------- Apply later
  const LATER_REASONS = ["Account / login issue", "Needs the laptop", "Long company form", "Waiting for a referral", "Need to update resume first", "Other"];
  function askLater(job, card) {
    const dlg = $("#later-dlg");
    $("#later-job").textContent = `${job.title} · ${job.company}. Why are you parking it?`;
    $("#later-reasons").replaceChildren(...LATER_REASONS.map((r) => {
      const b = el("button", { type: "button", className: "btn reason", textContent: r });
      b.addEventListener("click", () => {
        dlg.close();
        moveJob(job, "later", card, { reason: r });
      });
      return b;
    }));
    openOverlay(dlg);
  }

  // ----------------------------------------------------- fit / gap line
  function fitLines(job) {
    const m = job.match;
    if (!m) {
      const k = job.matched_keywords || [];
      return [k.length ? `Why: ${k.length} of your skills (${k.slice(0, 4).join(", ")})` : "", ""];
    }
    const why = [];
    why.push(m.role_fit === "match" ? "role matches" : m.role_fit === "related" ? "related role" : "different role");
    if (m.matched_skills?.length) why.push(`${m.matched_skills.length} of your skills`);
    if (m.experience?.min != null && m.experience.fits) why.push("experience fits");
    if (m.location?.fits && m.location.label) why.push(m.location.label);
    const gap = [];
    if (m.missing_skills?.length) gap.push(`not on your profile: ${m.missing_skills.slice(0, 3).join(", ")}${m.missing_skills.length > 3 ? ` +${m.missing_skills.length - 3}` : ""}`);
    if (m.experience?.min != null && !m.experience.fits) gap.push(`asks ${m.experience.min}${m.experience.max ? `–${m.experience.max}` : "+"} yrs`);
    if (m.qualifications?.fits === false) gap.push(`asks a ${m.qualifications.degree_asked} degree`);
    return [`Why: ${why.join(" · ")}`, gap.length ? `Gap: ${gap.join(" · ")}` : "Gap: none spotted in the listing"];
  }

  // ---------------------------------------------------- failed sources
  // Same wording as tasks.KNOWN_BLOCKS, for jobs.json (single-user) runs
  const KNOWN_BLOCKS = [
    [/naukri.*406|406.*naukri|request token/i, "Naukri is refusing automated searches (it changed its request check). Nothing to fix on your side; Naukri jobs return when the JobSpy library is updated"],
    [/google returned no job data/i, "Google Jobs isn't returning results to automated searches"],
    [/glassdoor.*403|403.*glassdoor/i, "Glassdoor blocks searches from the server (403)"],
  ];
  const plain = (source, reason) => (KNOWN_BLOCKS.find(([rx]) => rx.test(`${source} ${reason}`)) || [null, reason])[1];
  function failedSources() {
    if (data.run?.failed) return data.run.failed;
    const runs = data.stats?.source_runs || {};  // jobs.json (single-user): same rule as tasks.failed_sources
    const ok = ["ok", "disabled", "not permitted", "throttled", "skipped"];
    const out = [];
    for (const [name, r] of Object.entries(runs)) {
      if (!ok.includes(r.status)) out.push({ source: name, reason: plain(name, String(r.error || r.status)).slice(0, 200) });
      else if (name === "jobspy") {
        const seen = new Set();
        for (const w of r.warnings || []) {
          const site = w.split(" ")[0];
          if (!seen.has(site)) {
            seen.add(site);
            out.push({ source: site, reason: plain(site, w.split(": ").slice(1).join(": ")).slice(0, 200) });
          }
        }
      }
    }
    return out;
  }

  // ------------------------------------------------------- skill match
  const lower = (xs) => new Set((xs || []).map((x) => String(x).toLowerCase()));
  function missingSkills() {
    const p = JRAccount.profile();
    const mine = lower([...(p.skills || []), ...(data.skills_scan?.implied || [])]), hidden = lower(p.hidden_skills);
    return (data.skills_scan?.skills || []).filter(([k]) => !mine.has(k.toLowerCase()) && !hidden.has(k.toLowerCase()));
  }
  let showAllSkills = false;
  let busySkill = false;

  async function editSkill(action, skill) {
    if (readOnly) return toast(`Not saved: ${readOnly}`);
    if (busySkill) return;
    busySkill = true;
    const me = JRAccount.me;
    const before = me ? JSON.parse(JSON.stringify(me.profile || {})) : null;
    const jobsBefore = data.jobs;
    const same = (xs) => (xs || []).filter((x) => x.toLowerCase() !== skill.toLowerCase());
    // Show it straight away: your list, and on every job the skill moves between missing and matched
    if (me) {
      const p = me.profile = { ...me.profile };
      if (action === "add") Object.assign(p, { skills: [...same(p.skills), skill], added_skills: [...same(p.added_skills), skill], hidden_skills: same(p.hidden_skills) });
      if (action === "remove") Object.assign(p, { skills: same(p.skills), added_skills: same(p.added_skills) });
      if (action === "hide") p.hidden_skills = [...same(p.hidden_skills), skill];
      if (action === "unhide") p.hidden_skills = same(p.hidden_skills);
    }
    if (action === "add" || action === "remove") {
      data.jobs = data.jobs.map((j) => {
        const m = j.match;
        if (!m) return j;
        const has = (xs) => (xs || []).some((x) => x.toLowerCase() === skill.toLowerCase());
        if (action === "add" && has(m.missing_skills)) {
          const matched = [...(m.matched_skills || []), skill];
          return { ...j, matched_keywords: matched, match: { ...m, matched_skills: matched, missing_skills: same(m.missing_skills) } };
        }
        if (action === "remove" && has(m.matched_skills)) {
          const matched = same(m.matched_skills);
          return { ...j, matched_keywords: matched, match: { ...m, matched_skills: matched, missing_skills: [skill, ...(m.missing_skills || [])] } };
        }
        return j;
      });
    }
    render();
    try {
      const r = await JRAccount.api("/api/skills", { method: "POST", body: { action, skill } });
      if (me) me.profile = r.profile;
      toast({ add: `${skill} added to your skills${r.count != null ? ` · matches re-scored (${r.count})` : ""}`,
        remove: `${skill} removed from your skills`, hide: `${skill} hidden from the list`, unhide: `${skill} shown again` }[action]);
      if (action === "add" || action === "remove") load({ quiet: true });  // fresh scores from the server
      else render();
    } catch (e) {
      if (me && before) me.profile = before;
      data.jobs = jobsBefore;
      render();
      toast(`Couldn't save: ${e.message}`);
    } finally {
      busySkill = false;
    }
  }

  function renderSkills() {
    const wrap = el("div", { className: "panel-list skills-page" });
    if (!JRAccount.enabled()) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "Sign in to see your skill match" }),
        "It compares your profile's skills with what recent postings for your roles ask for."));
      return wrap;
    }
    const scan = data.skills_scan;
    const p = JRAccount.profile();
    if (!scan || !scan.scanned) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No scan yet" }),
        scan ? "No recent postings matched your roles and experience yet. The next run (every 3 hours) tries again."
          : "It's worked out with your matches. Save your profile or wait for the next run."));
    } else {
      const band = scan.band || [];
      wrap.append(el("div", { className: "cov" },
        el("div", { className: `cov-ring ${scoreClass(scan.coverage ?? 0)}`, style: `--v:${scan.coverage ?? 0}` }, el("span", { textContent: `${scan.coverage ?? "–"}%` })),
        el("div", {},
          el("b", { textContent: "Skill coverage" }),
          el("p", { textContent: `Of ${scan.mentions} skill mentions in recent postings for your roles, ${scan.coverage}% are skills on your profile.` }),
          el("p", { className: "muted", textContent: `${scan.scanned} postings scanned · last ${scan.days} days · ${band[0] ?? 0}–${band[1] ?? "?"} yrs experience · updated ${ago(scan.updated)}` }))));
      const miss = missingSkills();
      const top = Math.max(1, ...miss.map(([, n]) => n));
      wrap.append(el("h3", { className: "panel-h", textContent: `Missing skills (${miss.length})` }),
        el("p", { className: "muted", textContent: "Ranked by how many postings ask for them. Add only skills you really have: added skills count toward your fit score." }));
      const rows = (showAllSkills ? miss : miss.slice(0, 12)).map(([k, n]) => {
        const add = el("button", { className: "btn btn-primary", type: "button", textContent: "+ Add" });
        add.addEventListener("click", () => editSkill("add", k));
        const hide = el("button", { className: "btn", type: "button", textContent: "Hide" });
        hide.addEventListener("click", () => editSkill("hide", k));
        return el("div", { className: "skill-row" },
          el("div", { className: "skill-name" }, el("b", { textContent: k }), el("span", { className: "muted", textContent: ` asked in ${n} posting${n > 1 ? "s" : ""}` }),
            el("div", { className: "skill-bar" }, el("i", { style: `width:${Math.round((100 * n) / top)}%` }))),
          el("div", { className: "row" }, add, hide));
      });
      wrap.append(...rows);
      if (!miss.length) wrap.append(el("p", { className: "muted", textContent: "Every skill these postings mention is on your profile (or hidden)." }));
      if (miss.length > 12) {
        const more = el("button", { className: "btn show-all", type: "button", textContent: showAllSkills ? "Show fewer" : `Show all ${miss.length}` });
        more.dataset.safe = "1";
        more.addEventListener("click", () => {
          showAllSkills = !showAllSkills;
          render();
        });
        wrap.append(more);
      }
    }
    const added = p.added_skills || [];
    wrap.append(el("h3", { className: "panel-h", textContent: `Skills you added (${added.length})` }));
    wrap.append(added.length ? el("div", { className: "chips" }, ...added.map((k) => {
      const b = el("button", { type: "button", className: "chip", title: "Remove from your skills" }, k, el("span", { className: "chip-count", textContent: "×" }));
      b.setAttribute("aria-pressed", "true");
      b.addEventListener("click", () => editSkill("remove", k));
      return b;
    })) : el("p", { className: "muted", textContent: "None yet. Skills you add here (or with + on a job) show up here." }));
    const hidden = p.hidden_skills || [];
    if (hidden.length) {
      const open = !isCollapsed("hiddenSkills", true);
      const t = el("button", { type: "button", className: "link-btn", textContent: `${open ? "▾" : "▸"} Hidden skills (${hidden.length})` });
      t.dataset.safe = "1";
      t.addEventListener("click", () => {
        setCollapsed("hiddenSkills", open);
        render();
      });
      wrap.append(t);
      if (open) {
        wrap.append(el("div", { className: "chips" }, ...hidden.map((k) => {
          const b = el("button", { type: "button", className: "chip", title: "Show in the list again" }, k, el("span", { className: "chip-count", textContent: "↺" }));
          b.addEventListener("click", () => editSkill("unhide", k));
          return b;
        })));
      }
    }
    return wrap;
  }

  // ---------------------------------------------------------- pipeline
  const PIPE_COLS = [["company", "Company & role"], ["source", "Source"], ["city", "City"], ["fit", "Fit"], ["skills", "Matched skills", false],
    ["found", "Found"], ["status", "Status"], ["applied", "Applied"]];
  const pipeKey = {
    company: (j) => String(j.company || "").toLowerCase(), source: (j) => SOURCE_LABELS[j.source] || j.source || "",
    city: (j) => locationLabel(j), fit: (j) => j.score ?? -1, found: (j) => foundAt(j),
    status: (j) => STAGE_ORDER.indexOf(statusOf(j)), applied: (j) => store.statuses[j.id]?.appliedAt || "",
  };
  function renderPipeline(jobs) {
    const rows = sectionJobs("pipeline", jobs);
    const sort = readJSON("jobradar.pipeSort", null);
    const cmp = sort && pipeKey[sort.key]
      ? (a, b) => {
          const x = pipeKey[sort.key](a), y = pipeKey[sort.key](b);
          return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * (sort.dir === "desc" ? -1 : 1) || byFit(a, b);
        }
      : (a, b) => STAGE_ORDER.indexOf(statusOf(a)) - STAGE_ORDER.indexOf(statusOf(b)) || byFit(a, b);
    rows.sort(cmp);
    const wrap = el("div", { className: "pipeline" });
    const reset = sort ? el("button", { type: "button", className: "link-btn", textContent: "Default order (by stage)" }) : null;
    if (reset) {
      reset.dataset.safe = "1";
      reset.addEventListener("click", () => {
        writeJSON("jobradar.pipeSort", undefined);
        render();
      });
    }
    wrap.append(el("p", { className: "muted" }, `${rows.length} of ${jobs.length} jobs`, reset ? " · " : "", reset || ""));
    if (!jobs.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No jobs yet" }), "Matches and the jobs you track appear here."));
      return wrap;
    }
    if (!rows.length) {
      wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No jobs match these filters" }), "Clear filters to see everything."));
      return wrap;
    }
    const head = el("tr", {}, ...PIPE_COLS.map(([key, label, sortable]) => {
      if (sortable === false) return el("th", { textContent: label });
      const on = sort?.key === key;
      const b = el("button", { type: "button", className: "th-sort", textContent: `${label}${on ? (sort.dir === "desc" ? " ↓" : " ↑") : ""}` });
      b.dataset.safe = "1";
      b.addEventListener("click", () => {
        writeJSON("jobradar.pipeSort", { key, dir: on && sort.dir === "asc" ? "desc" : on ? "asc" : ["fit", "found", "applied"].includes(key) ? "desc" : "asc" });
        render();
      });
      const th = el("th", {}, b);
      th.setAttribute("aria-sort", on ? (sort.dir === "desc" ? "descending" : "ascending") : "none");
      return th;
    }));
    const body = rows.map((j) => {
      const st = statusOf(j);
      const applied = store.statuses[j.id]?.appliedAt;
      const tr = el("tr", { tabIndex: 0 },
        el("td", {}, el("b", { textContent: j.company }), el("div", { className: "muted", textContent: j.title })),
        el("td", {}, el("span", { className: "src-dot", style: `background:${sourceColor(j.source)}` }), ` ${SOURCE_LABELS[j.source] || j.source}`),
        el("td", { textContent: locationLabel(j) }),
        el("td", {}, j.score == null ? "–" : el("span", { className: `fit-pill ${scoreClass(j.score)}`, textContent: j.score })),
        el("td", { className: "muted", textContent: (j.matched_keywords || []).slice(0, 4).join(", ") }),
        el("td", { textContent: fmtDate(j.first_seen || j.posted_date) }),
        el("td", {}, el("span", { className: `st-chip st-${st}`, textContent: isPending(j) ? "Pending" : sectionLabel(st) })),
        el("td", { textContent: applied && APPLIED.includes(st) ? fmtDate(applied) : "" }));
      tr.dataset.safe = "1";
      tr.addEventListener("click", () => focusJob(j.id));
      tr.addEventListener("keydown", (e) => e.key === "Enter" && focusJob(j.id));
      return tr;
    });
    wrap.append(el("div", { className: "table-wrap" }, el("table", { className: "pipe-table" }, el("thead", {}, head), el("tbody", {}, ...body))));
    return wrap;
  }

  return { deviceOf, deviceWhy, companyKey, isCollapsed, setCollapsed, applyBar, copyAnswers, askLater, fitLines,
    failedSources, missingSkills, editSkill, renderSkills, renderPipeline, missingAnswers };
})();
