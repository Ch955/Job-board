/* Job Radar app. Jobs come from jobs.json (rebuilt every 3 hours by GitHub
   Actions). Your statuses live on this device and, once you sign in, in an
   encrypted file in your repo so every device shows the same thing (sync.js). */
"use strict";

// ------------------------------------------------------------------- config
const K = {
  store: "jobradar.v1",
  filters: "jobradar.filters",
  theme: "jobradar.theme",
  section: "jobradar.section",
  sync: "jobradar.sync",       // {owner, repo, branch, token, key, salt, iter} when "keep me signed in"
  mode: "jobradar.mode",       // "local" = chose to use without sync
  dirty: "jobradar.dirty",     // unsynced local changes
};
const APPLIED = ["applied", "interview", "offer", "rejected"];
const KEEP = ["later", "applied", "interview", "offer", "rejected"];
const DEFAULT_FILTERS = { q: "", minScore: 40, location: [], source: [], remote: [] };
const MULTI = ["source", "location", "remote"];
const SOURCE_LABELS = { naukri: "Naukri", linkedin: "LinkedIn", indeed: "Indeed", glassdoor: "Glassdoor", google: "Google",
  greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", smartrecruiters: "SmartRecruiters", adzuna: "Adzuna",
  jooble: "Jooble", remotive: "Remotive", arbeitnow: "Arbeitnow", email: "Gmail" };
const WORK_MODES = [["remote", "Remote"], ["onsite", "On-site / hybrid"]];

const SECTIONS = [
  { id: "new", label: "To apply", icon: "🎯", tile: "var(--primary)", hint: "New matches, best first. Tap Applied once you've submitted." },
  { id: "later", label: "Apply later", icon: "⏳", tile: "var(--s-mid)", hint: "Parked: company sites, account issues, or apply from the laptop." },
  { id: "applied", label: "Applied", icon: "✅", tile: "var(--good)", hint: "Newest first. Gmail confirmations land here by themselves." },
  { id: "interview", label: "Interview", icon: "🗓️", tile: "var(--interview)", hint: "Tests and interview rounds in progress." },
  { id: "offer", label: "Offers", icon: "🎉", tile: "var(--good)", hint: "" },
  { id: "rejected", label: "Rejected", icon: "✖️", tile: "var(--done)", hint: "Kept for your records." },
  { id: "skipped", label: "Skipped", icon: "🙈", tile: "var(--done)", hint: "You can still apply from here: tap Apply, then Applied." },
];
const GMAIL_SECTIONS = [
  { id: "mail", label: "Recruiter emails", icon: "✉️", tile: "var(--primary)" },
  { id: "reply", label: "Inbox updates", icon: "📥", tile: "var(--interview)" },
];
// Main buttons for jobs you haven't applied to, and "move to" buttons for the rest
const MAIN_ACTIONS = {
  new: [["applied", "Applied"], ["later", "Later"], ["skipped", "Skip"]],
  later: [["applied", "Applied"], ["skipped", "Skip"]],
  skipped: [["applied", "Applied"], ["new", "↩ To apply"]],
};
const MOVE_ACTIONS = {
  later: [["new", "↩ To apply"]],
  applied: [["interview", "Interview"], ["rejected", "Rejected"], ["new", "↩ Undo"]],
  interview: [["offer", "Offer"], ["rejected", "Rejected"], ["applied", "↩ Applied"]],
  offer: [["interview", "↩ Interview"]],
  rejected: [["applied", "↩ Applied"], ["interview", "Interview"]],
};
const ALL_SECTIONS = [...SECTIONS, ...GMAIL_SECTIONS];
const sectionLabel = (id) => ALL_SECTIONS.find((x) => x.id === id)?.label || id;

const $ = (sel, root = document) => root.querySelector(sel);

let data = { jobs: [] };
let filters = { ...DEFAULT_FILTERS };
let section = "new";
let lastFetch = 0;
let animateNext = true;
let reloading = false;
let session = null; // {owner, repo, branch, token, key(CryptoKey), salt, iter, gh}
let syncInfo = { state: "off", at: null, error: "" };

// ------------------------------------------------------------------ storage
function readJSON(key, fallback, area = localStorage) {
  try {
    const raw = area.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}
function writeJSON(key, value, area = localStorage) {
  try {
    if (value === undefined) area.removeItem(key);
    else area.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode / storage full: the app keeps working in memory */
  }
}

let store = JRSync.normalize(readJSON(K.store, {}));
const saveLocal = () => writeJSON(K.store, store);

function statusOf(job) {
  return store.statuses[job.id]?.status || job.status || "new";
}
function isPending(job) {
  const p = store.pending[job.id];
  if (!p?.open || APPLIED.includes(statusOf(job))) return false;
  const rec = store.statuses[job.id];
  return !rec || String(rec.at) <= String(p.at);
}
const stateOf = (job) => (isPending(job) ? "pending" : statusOf(job));

function snapshot(job) {
  const { id, title, company, location, url, source, score, matched_keywords, posted_date, first_seen, remote,
          score_breakdown, description_snippet, outreach, contact_emails } = job;
  return { id, title, company, location, url, source, score, matched_keywords, posted_date, first_seen, remote,
           score_breakdown, description_snippet, outreach, contact_emails };
}

function setStatus(job, status) {
  const prev = store.statuses[job.id];
  const now = new Date().toISOString();
  const rec = { status, at: now };
  const appliedAt = prev?.appliedAt || (APPLIED.includes(status) ? now : null);
  if (appliedAt) rec.appliedAt = appliedAt;
  store.statuses[job.id] = rec; // "new" is a record too, so an undo syncs to other devices
  if (KEEP.includes(status)) store.saved[job.id] = snapshot(job);
  else delete store.saved[job.id];
  if (store.pending[job.id]?.open) store.pending[job.id] = { at: now, open: false };
  changed();
}

function changed() {
  saveLocal();
  if (session) {
    writeJSON(K.dirty, true);
    scheduleSync();
  }
}

// -------------------------------------------------------------------- utils
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}
function safeUrl(url) {
  try {
    const u = new URL(url, location.href);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(s)) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d}d ago`;
}
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "");
function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}
function startOfWeek() {
  const d = startOfToday();
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // Monday
  return d;
}
const scoreClass = (s) => (s >= 85 ? "high" : s >= 70 ? "mid" : "low");

let toastTimer;
function toast(message, action) {
  const t = $("#toast");
  t.replaceChildren(document.createTextNode(message));
  if (action) {
    const b = el("button", { type: "button", textContent: action.label });
    b.addEventListener("click", () => {
      t.hidden = true;
      action.run();
    });
    t.append(b);
  }
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), action ? 7000 : 3200);
}

function countUp(node, value) {
  const from = Number(node.textContent) || 0;
  if (from === value || matchMedia("(prefers-reduced-motion: reduce)").matches) {
    node.textContent = value;
    return;
  }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / 400);
    node.textContent = Math.round(from + (value - from) * (1 - (1 - k) ** 3));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ------------------------------------------------------------- history/back
// Back closes the drawer or a sheet first, then walks back through sections,
// instead of leaving the app.
let ignorePop = false;
function pushView(state) {
  // A menu that was just closed in code (to open a section or another sheet) leaves an
  // entry behind: reuse it so Back never needs an extra press
  const stale = history.state?.overlay && !document.getElementById(history.state.overlay)?.open;
  if (stale) history.replaceState(state, "", location.pathname + location.search);
  else history.pushState(state, "", location.pathname + location.search);
}
function openOverlay(dialog) {
  if (dialog.open) return;
  dialog.showModal();
  pushView({ overlay: dialog.id, s: section });
}
function closeOverlay(dialog) {
  if (dialog.open) dialog.close();
}
function onOverlayClosed(dialog) {
  // Closed by ✕, Esc or a tap outside: drop the history entry we added
  if (history.state?.overlay === dialog.id) {
    ignorePop = true;
    history.back();
  }
}
window.addEventListener("popstate", (e) => {
  if (ignorePop) {
    ignorePop = false;
    return;
  }
  const open = [...document.querySelectorAll("dialog[open]")];
  if (open.length) {
    open.forEach((d) => d.close());
    return;
  }
  const s = e.state?.s;
  if (s && s !== section) setSection(s, { history: false });
});

// ------------------------------------------------------------------- data
async function load({ quiet = false } = {}) {
  $("#refresh").classList.add("spinning");
  try {
    const r = await fetch(`jobs.json?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
    data.jobs = Array.isArray(data.jobs) ? data.jobs : [];
    lastFetch = Date.now();
    if (!quiet) toast(`Updated · ${data.jobs.length} jobs`);
  } catch {
    if (!quiet) toast(navigator.onLine ? "Couldn't load jobs" : "Offline: showing saved jobs");
  } finally {
    $("#refresh").classList.remove("spinning");
  }
  render();
}

function allJobs() {
  const ids = new Set(data.jobs.map((j) => j.id));
  return data.jobs.concat(Object.values(store.saved).filter((j) => j?.id && !ids.has(j.id)));
}
const locationLabel = (job) => job.score_breakdown?.location_label || (job.remote ? "Remote (India)" : job.location || "India");
const modeOf = (job) => (job.remote ? "remote" : "onsite");
const FIELD_VALUE = { source: (j) => j.source, location: locationLabel, remote: modeOf };

// ------------------------------------------------------------------ filters
function matchesFilters(job, ignore) {
  const status = statusOf(job);
  if ((job.score ?? 100) < filters.minScore && (status === "new" || status === "skipped")) return false;
  for (const k of MULTI) {
    if (k !== ignore && filters[k].length && !filters[k].includes(FIELD_VALUE[k](job))) return false;
  }
  if (filters.q) {
    const hay = `${job.title} ${job.company} ${job.location} ${(job.matched_keywords || []).join(" ")}`.toLowerCase();
    if (!filters.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}
function normalizeFilters(f) {
  const out = { ...DEFAULT_FILTERS, ...f };
  for (const k of MULTI) {
    const v = out[k];
    out[k] = Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];
  }
  delete out.status;
  return out;
}
function setFilters(patch) {
  filters = { ...filters, ...patch };
  writeJSON(K.filters, filters);
  render();
}
const activeFilterCount = () => (filters.minScore !== DEFAULT_FILTERS.minScore ? 1 : 0) + MULTI.filter((k) => filters[k].length).length;

function buildFilterChips() {
  const all = allJobs();
  const inSection = all.filter((j) => statusOf(j) === section);
  const groups = {
    source: [...new Set(all.map((j) => j.source))].sort().map((v) => [v, SOURCE_LABELS[v] || v]),
    location: [...new Set(all.map(locationLabel))].sort().map((v) => [v, v]),
    remote: WORK_MODES,
  };
  const focused = document.activeElement?.dataset?.chip;
  for (const key of MULTI) {
    const base = inSection.filter((j) => matchesFilters(j, key));
    $(`#f-${key}`).replaceChildren(...groups[key].map(([value, label]) => {
      const on = filters[key].includes(value);
      const b = el("button", { type: "button", className: "chip" }, label,
        el("span", { className: "chip-count", textContent: base.filter((j) => FIELD_VALUE[key](j) === value).length }));
      b.dataset.chip = `${key}:${value}`;
      b.setAttribute("aria-pressed", String(on));
      b.addEventListener("click", () => setFilters({ [key]: on ? filters[key].filter((x) => x !== value) : [...filters[key], value] }));
      return b;
    }));
  }
  if (focused) document.querySelector(`[data-chip="${CSS.escape(focused)}"]`)?.focus();
}
function applyFiltersToInputs() {
  $("#f-search").value = filters.q;
  $("#f-score").value = filters.minScore;
  $("#f-score-val").textContent = filters.minScore;
}

// ------------------------------------------------------------------- render
function sectionJobs(id, jobs = allJobs()) {
  return jobs.filter((j) => statusOf(j) === id && matchesFilters(j));
}
function sortFor(id) {
  const at = (j) => store.statuses[j.id]?.at || "";
  if (id === "applied") return (a, b) => (store.statuses[b.id]?.appliedAt || "").localeCompare(store.statuses[a.id]?.appliedAt || "");
  if (["interview", "offer", "rejected", "later", "skipped"].includes(id)) return (a, b) => at(b).localeCompare(at(a));
  // To apply: jobs you opened but didn't confirm come first, then by score
  return (a, b) => (isPending(b) - isPending(a)) || (b.score || 0) - (a.score || 0) ||
    String(b.posted_date || b.first_seen || "").localeCompare(String(a.posted_date || a.first_seen || ""));
}

function appliedTimes() {
  return Object.values(store.statuses).filter((s) => APPLIED.includes(s.status) && s.appliedAt).map((s) => new Date(s.appliedAt));
}

function render() {
  const jobs = allJobs();
  const isGmail = GMAIL_SECTIONS.some((g) => g.id === section);
  const shown = isGmail ? [] : sectionJobs(section, jobs).sort(sortFor(section));
  const list = $("#list");

  $("#section-name").textContent = sectionLabel(section);
  $("#section-count").textContent = isGmail ? "–" : shown.length;
  $("#section-hint").textContent = isGmail ? "" : SECTIONS.find((x) => x.id === section)?.hint || "";
  $(".filter-row").hidden = isGmail;
  $("#filter-panel").hidden = isGmail || $("#toggle-filters").getAttribute("aria-expanded") !== "true";
  document.title = `${sectionLabel(section)} · Job Radar`;

  if (isGmail) {
    list.replaceChildren(section === "mail" ? renderMailSection() : renderInboxSection());
  } else if (!shown.length) {
    const any = jobs.some((j) => statusOf(j) === section);
    list.replaceChildren(el("div", { className: "empty" },
      el("b", { textContent: any ? "No jobs match these filters" : !data.jobs.length && section === "new" ? "No jobs yet" : `Nothing in ${sectionLabel(section)} yet` }),
      any ? "Try lowering the minimum score or resetting filters."
        : section === "new" ? "The next run (every 3 hours) fills this in."
        : section === "applied" ? "Tap Applied on a job and it moves here." : ""));
  } else {
    list.replaceChildren(...shown.map((job, i) => renderCard(job, i)));
  }
  animateNext = false;

  const times = appliedTimes();
  const today = startOfToday(), week = startOfWeek();
  countUp($("#stat-today"), times.filter((d) => d >= today).length);
  countUp($("#stat-week"), times.filter((d) => d >= week).length);
  countUp($("#stat-toapply"), sectionJobs("new", jobs).length);
  countUp($("#stat-interview"), jobs.filter((j) => statusOf(j) === "interview").length);
  $("#updated").textContent = data.generated_at ? `Jobs updated ${ago(data.generated_at)} · ${data.jobs.length} found` : "Waiting for the first run";

  const n = activeFilterCount();
  $("#toggle-filters").textContent = n ? `Filters (${n})` : "Filters";
  $("#toggle-filters").classList.toggle("has-filters", n > 0);
  if (!isGmail) buildFilterChips();
  renderNav(jobs);
  renderSyncBadges();
}

function renderCard(job, index) {
  const card = $("#card-tpl").content.firstElementChild.cloneNode(true);
  const status = statusOf(job);
  const pending = isPending(job);
  const applied = APPLIED.includes(status);
  const rec = store.statuses[job.id];
  card.id = `job-${job.id}`;
  card.dataset.state = stateOf(job);
  if (animateNext) {
    card.classList.add("enter");
    card.style.setProperty("--i", Math.min(index, 10));
  }

  const score = $(".score", card);
  if (job.score == null) {
    score.remove();
  } else {
    score.style.setProperty("--v", job.score);
    score.classList.add(scoreClass(job.score));
    $("span", score).textContent = job.score;
  }
  $(".title", card).textContent = job.title;
  $(".company", card).textContent = job.company;

  const meta = $(".meta", card);
  if (status === "new" && !pending && new Date(job.first_seen).getTime() >= Date.now() - 86400e3) meta.append(el("span", { className: "badge", textContent: "new" }));
  if (job.score_breakdown?.walk_in) meta.append(el("span", { className: "badge walkin", textContent: "walk-in" }));
  if (rec?.source === "gmail") meta.append(el("span", { className: "badge gmail", textContent: "from Gmail" }));
  for (const p of [job.location, job.remote ? "Remote" : null, SOURCE_LABELS[job.source] || job.source, ago(job.posted_date || job.first_seen)].filter(Boolean)) {
    meta.append(el("span", { textContent: p }));
  }

  const kw = $(".keywords", card);
  for (const k of job.matched_keywords || []) kw.append(el("li", { textContent: k }));
  const snippet = $(".snippet", card);
  snippet.textContent = job.description_snippet || "";
  snippet.addEventListener("click", () => snippet.classList.toggle("open"));

  const bd = $(".breakdown", card);
  const b = job.score_breakdown || {};
  for (const [label, value] of [["Title", b.title], ["Skills", b.skills], ["Location", b.location], ["Experience", b.experience], ["Recency", b.recency], ["Penalty", b.penalty]]) {
    if (value !== undefined) bd.append(el("span", {}, `${label} `, el("b", { textContent: value })));
  }
  if (b.years_required != null) bd.append(el("span", { textContent: `Needs ${b.years_required}${b.years_max ? `–${b.years_max}` : "+"} yrs` }));
  $(".score", card)?.addEventListener("click", () => (bd.hidden = !bd.hidden));

  // Pending: you opened the posting but haven't confirmed
  if (pending) {
    const bar = $(".pending-bar", card);
    bar.hidden = false;
    $(".pending-text", bar).textContent = `Opened ${ago(store.pending[job.id].at)}. Did you apply?`;
    $(".pb-yes", bar).addEventListener("click", () => moveJob(job, "applied", card));
    $(".pb-no", bar).addEventListener("click", () => {
      store.pending[job.id] = { at: new Date().toISOString(), open: false };
      changed();
      render();
    });
  }

  // Apply: blue to start, amber once opened, grey after you've applied
  const actions = $(".card-actions", card);
  const apply = $(".apply", card);
  const url = safeUrl(job.url);
  apply.textContent = applied ? "View job" : pending ? "⏳ Pending" : "Apply";
  apply.classList.toggle("pending", pending);
  apply.classList.toggle("done", applied);
  if (url) {
    apply.href = url;
    apply.addEventListener("click", () => {
      if (APPLIED.includes(statusOf(job))) return;
      store.pending[job.id] = { at: new Date().toISOString(), open: true };
      changed();
      // Repaint for when you come back from the job site
      setTimeout(() => {
        render();
        toast("Did you apply?", { label: "Yes, applied", run: () => moveJob(job, "applied") });
      }, 500);
    });
  } else {
    apply.removeAttribute("href");
    apply.setAttribute("aria-disabled", "true");
  }

  if (applied) {
    const done = el("button", { className: "btn is-applied", type: "button", textContent: `✓ Applied${rec?.appliedAt ? " " + fmtDate(rec.appliedAt) : ""}` });
    done.addEventListener("click", () => toast(`Applied ${ago(rec?.appliedAt)}${rec?.source === "gmail" ? " · confirmed by Gmail" : ""}`));
    actions.append(done);
  }
  for (const [to, label] of MAIN_ACTIONS[status] || []) {
    const btn = el("button", { className: `btn${to === "applied" ? " go-applied" : ""}`, type: "button", textContent: label });
    btn.dataset.to = to;
    btn.addEventListener("click", () => moveJob(job, to, card));
    actions.append(btn);
  }
  const move = $(".move-row", card);
  const moves = MOVE_ACTIONS[status] || [];
  if (moves.length) {
    move.append(el("span", { className: "lbl", textContent: "Move to" }));
    for (const [to, label] of moves) {
      const btn = el("button", { className: "move-btn", type: "button", textContent: label });
      btn.dataset.to = to;
      btn.addEventListener("click", () => moveJob(job, to, card));
      move.append(btn);
    }
  }
  renderOutreach(job, status, $(".outreach-row", card));
  return card;
}

function moveJob(job, to, card) {
  const before = statusOf(job);
  const prevRec = store.statuses[job.id] ? { ...store.statuses[job.id] } : null;
  const prevSaved = store.saved[job.id];
  const prevPending = store.pending[job.id] ? { ...store.pending[job.id] } : null;
  const finish = () => {
    setStatus(job, to);
    render();
    toast(to === "new" ? "Back in To apply" : `Moved to ${sectionLabel(to)}`, {
      label: "Undo",
      run: () => {
        // The undo is itself a change (so it syncs), restoring the earlier status and applied time
        const now = new Date().toISOString();
        store.statuses[job.id] = prevRec ? { ...prevRec, at: now } : { status: before, at: now };
        if (prevSaved) store.saved[job.id] = prevSaved; else if (!KEEP.includes(before)) delete store.saved[job.id];
        if (prevPending) store.pending[job.id] = { ...prevPending, at: now };
        changed();
        render();
      },
    });
  };
  if (card && to !== before && card.isConnected) {
    card.classList.add("leaving");
    setTimeout(finish, 200);
  } else {
    finish();
  }
}

// ---------------------------------------------------------------- navigation
function setSection(id, { history: push = true } = {}) {
  if (!ALL_SECTIONS.some((s) => s.id === id)) id = "new";
  const moved = id !== section;
  section = id;
  writeJSON(K.section, id);
  if (push && moved) pushView({ s: id });
  animateNext = true;
  render();
  if (moved) window.scrollTo({ top: 0 });
}

function renderNav(jobs) {
  const counts = {};
  for (const j of jobs) {
    const st = statusOf(j);
    if (st === "new" && !matchesFilters(j)) continue;
    counts[st] = (counts[st] || 0) + 1;
  }
  const item = (sec, count) => {
    const b = el("button", { type: "button", className: "nav-item" },
      el("span", { className: "nav-icon", textContent: sec.icon }),
      el("span", { className: "nav-label", textContent: sec.label }),
      count === null ? null : el("span", { className: "count-pill", textContent: count }));
    b.style.setProperty("--tile", sec.tile);
    b.setAttribute("aria-current", String(sec.id === section));
    b.addEventListener("click", () => {
      closeOverlay($("#nav"));
      setSection(sec.id);
    });
    return b;
  };
  const tool = (icon, label, run) => {
    const b = el("button", { type: "button", className: "nav-item" },
      el("span", { className: "nav-icon", textContent: icon }), el("span", { className: "nav-label", textContent: label }));
    b.addEventListener("click", run);
    return b;
  };
  $("#nav-list").replaceChildren(
    el("p", { className: "nav-group", textContent: "Jobs" }),
    ...SECTIONS.map((sec) => item(sec, counts[sec.id] || 0)),
    el("p", { className: "nav-group", textContent: "Gmail" }),
    item(GMAIL_SECTIONS[0], jobs.filter((j) => j.outreach?.draft_id).length),
    item(GMAIL_SECTIONS[1], (store.gmail.events || []).length),
    el("p", { className: "nav-group", textContent: "Tools" }),
    tool("📊", "Export to Excel (.csv)", () => {
      closeOverlay($("#nav"));
      exportCSV("all");
    }),
    tool("⚙️", "Settings, alerts & backup", () => {
      closeOverlay($("#nav"));
      openSettings();
    }),
    tool(themeIcon(), `Theme: ${readJSON(K.theme, "auto")}`, () => {
      cycleTheme();
      renderNav(allJobs());
    }),
  );
  const p = data.outreach?.profile || {};
  const name = p.full_name || p.name || "Job Radar";
  $("#dr-avatar").textContent = name.split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "JR";
  $("#dr-name").textContent = p.name || name;
  $("#dr-role").textContent = [p.role, p.years_experience ? `${p.years_experience}+ yrs` : "", p.notice].filter(Boolean).join(" · ");
  const times = appliedTimes();
  const week = startOfWeek();
  countUp($("#dr-week"), times.filter((d) => d >= week).length);
  countUp($("#dr-total"), times.length);
  countUp($("#dr-int"), jobs.filter((j) => statusOf(j) === "interview").length);
  $("#dr-signout").textContent = session ? "Sign out" : "Sign in to sync";
}

// ------------------------------------------------------------------ outreach
const OUTREACH_STATUSES = ["new", "later", "applied", "interview", "skipped"];
function fillTemplate(tpl, job) {
  const p = data.outreach?.profile || {};
  const notice = p.notice || "";
  const mine = data.outreach?.my_skills || [];
  const skills = (job.matched_keywords || []).filter((k) => mine.includes(k) && !["SQL", "Python"].includes(k)).slice(0, 3);
  const values = {
    name: p.name || "", full_name: p.full_name || p.name || "", role: p.role || "", years: p.years_experience ?? "",
    notice, notice_lower: notice.charAt(0).toLowerCase() + notice.slice(1), linkedin: p.linkedin || "",
    email: "", title: job.title, company: job.company, url: job.url || "",
    location: job.score_breakdown?.location_label || job.location,
    skills: (skills.length ? skills : ["ADF", "Databricks", "PySpark"]).join(", "),
  };
  return String(tpl || "").replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "").trim();
}
function linkedinNote(job) {
  const tpl = data.outreach?.linkedin_note ||
    "Hi! I'm a {role} ({years}+ yrs). I've applied for {title} at {company} and would love to connect. Thanks, {name}";
  let note = fillTemplate(tpl, job);
  if (note.length > 200 && tpl.includes("{title}")) {
    const keep = Math.max(10, job.title.length - (note.length - 200) - 1);
    note = fillTemplate(tpl, { ...job, title: job.title.slice(0, keep).replace(/[\s,|/-]+$/, "") + "…" });
  }
  return note.length <= 200 ? note : note.slice(0, 199).trimEnd() + "…";
}
const recruiterSearch = (job) => `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${job.company} recruiter`)}&origin=GLOBAL_SEARCH_HEADER`;
const draftLink = (job) => job.outreach?.message_id
  ? `https://mail.google.com/mail/u/0/#drafts?compose=${encodeURIComponent(job.outreach.message_id)}`
  : "https://mail.google.com/mail/u/0/#drafts";
function mailtoLink(job) {
  const to = (job.contact_emails || [])[0];
  if (!to) return null;
  const subject = fillTemplate(data.outreach?.email_subject || "Application: {title}", job);
  const body = fillTemplate(data.outreach?.email_body || "", job);
  return `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
async function copyText(text, ok) {
  try {
    await navigator.clipboard.writeText(text);
    toast(ok);
  } catch {
    prompt("Copy this:", text);
  }
}
function renderOutreach(job, status, row) {
  if (!OUTREACH_STATUSES.includes(status) || job.source === "email") return;
  const link = (cls, text, href, title) => el("a", { className: `mini-btn ${cls}`, textContent: text, href, target: "_blank", rel: "noopener noreferrer", title });
  row.append(link("li", "👤 Find recruiter", recruiterSearch(job), "LinkedIn search for recruiters at this company"));
  const copy = el("button", { className: "mini-btn", type: "button", textContent: "📋 Copy note", title: "LinkedIn connection note (200 characters)" });
  copy.addEventListener("click", () => copyText(linkedinNote(job), "Note copied. LinkedIn: Connect → Add a note → paste"));
  row.append(copy);
  if (job.outreach?.draft_id) row.append(link("mail", store.sent[job.id] ? "✉️ Draft sent" : "✉️ Open draft", draftLink(job), `Draft to ${job.outreach.email}`));
  else if (mailtoLink(job)) row.append(link("mail", "✉️ Email", mailtoLink(job), `Write to ${job.contact_emails[0]}`));
}

function renderMailSection() {
  const jobs = allJobs().filter((j) => (j.outreach?.draft_id || (j.contact_emails || []).length) && statusOf(j) !== "skipped")
    .sort((a, b) => String(b.outreach?.drafted_at || "").localeCompare(String(a.outreach?.drafted_at || "")) || (b.score || 0) - (a.score || 0));
  const wrap = el("div", { className: "panel-list" });
  const drafts = data.stats?.drafts;
  if (drafts?.skipped && !jobs.some((j) => j.outreach?.draft_id)) {
    wrap.append(el("p", { className: "note", textContent: `Drafts aren't being created yet: ${drafts.skipped}. See the README: Gmail drafts. Meanwhile, Write email opens your mail app with the message ready.` }));
  }
  wrap.append(el("div", { className: "row" }, el("a", { className: "btn", href: "https://mail.google.com/mail/u/0/#drafts", target: "_blank", rel: "noopener", textContent: "All Gmail drafts ↗" })));
  if (!jobs.length) {
    wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No recruiter emails yet" }), "When a strong match lists a recruiter's email, it shows up here."));
    return wrap;
  }
  for (const job of jobs) {
    const sent = store.sent[job.id];
    const to = job.outreach?.email || job.contact_emails[0];
    const mark = el("button", { className: "btn", type: "button", textContent: sent ? "↩ Not sent" : "Mark sent" });
    mark.addEventListener("click", () => {
      if (sent) delete store.sent[job.id]; else store.sent[job.id] = new Date().toISOString();
      changed();
      render();
    });
    const url = safeUrl(job.url);
    wrap.append(el("article", { className: "panel-item" },
      el("div", { className: "panel-top" }, el("b", { textContent: job.company }),
        el("span", { className: `st-chip ${sent ? "st-applied" : job.outreach?.draft_id ? "st-interview" : "st-rejected"}`, textContent: sent ? "sent" : job.outreach?.draft_id ? "draft ready" : "no draft" })),
      el("p", { textContent: `${job.title}${job.score != null ? " · " + job.score : ""}` }),
      el("p", { className: "muted", textContent: `To ${to}${job.outreach?.drafted_at ? " · drafted " + ago(job.outreach.drafted_at) : ""}` }),
      el("div", { className: "row" },
        job.outreach?.draft_id
          ? el("a", { className: "btn btn-primary", href: draftLink(job), target: "_blank", rel: "noopener", textContent: "Open draft ↗" })
          : el("a", { className: "btn btn-primary", href: mailtoLink(job), textContent: "Write email" }),
        mark,
        url ? el("a", { className: "btn", href: url, target: "_blank", rel: "noopener", textContent: "Job ↗" }) : null)));
  }
  return wrap;
}

function renderInboxSection() {
  const wrap = el("div", { className: "panel-list" });
  const g = store.gmail || {};
  const events = g.events || [];
  if (!session) {
    wrap.append(el("p", { className: "note", textContent: "Sign in to sync first: Gmail updates are saved in your encrypted sync file." }));
  } else if (!g.last_check) {
    wrap.append(el("p", { className: "note", textContent: "Not connected to Gmail yet. Finish the Gmail setup in the README and add the SYNC_PASSPHRASE secret (the passphrase you sign in with). Then confirmations, interview invites and rejections update your jobs every 3 hours." }));
  } else {
    wrap.append(el("p", { className: "muted", textContent: `Gmail checked ${ago(g.last_check)} (read-only).` }));
  }
  if (!events.length) {
    wrap.append(el("div", { className: "empty" }, el("b", { textContent: "No inbox updates yet" }), "Application confirmations, interview invites and rejections appear here."));
    return wrap;
  }
  for (const ev of events) {
    const open = el("button", { className: "btn", type: "button", textContent: "Show job" });
    open.addEventListener("click", () => focusJob(ev.job_id));
    wrap.append(el("article", { className: "panel-item" },
      el("div", { className: "panel-top" }, el("b", { textContent: ev.company }), el("span", { className: `st-chip st-${ev.status}`, textContent: ev.status })),
      el("p", { textContent: ev.title }),
      el("p", { className: "muted", textContent: `${fmtDate(ev.at)} · ${ev.subject}` }),
      el("div", { className: "row" }, open)));
  }
  return wrap;
}

function focusJob(id) {
  const job = allJobs().find((j) => j.id === id);
  if (!job) return toast("That job is no longer in your list");
  filters = { ...DEFAULT_FILTERS };
  applyFiltersToInputs();
  setSection(statusOf(job));
  const card = document.getElementById(`job-${id}`);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.add("highlight");
    setTimeout(() => card.classList.remove("highlight"), 3000);
  }
}
function focusJobFromHash() {
  const m = location.hash.match(/^#job=([\w-]+)/);
  if (!m) return;
  history.replaceState({ s: section }, "", location.pathname + location.search);
  focusJob(m[1]);
}

// ------------------------------------------------------------------- export
function csvCell(v) {
  let s = v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // stop spreadsheet formula injection from job text
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function download(name, blob) {
  const a = el("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}
function exportCSV(scope) {
  const order = ["new", "later", "applied", "interview", "offer", "rejected", "skipped"];
  let jobs = allJobs();
  const inSection = scope === "section" && !GMAIL_SECTIONS.some((g) => g.id === section);
  if (inSection) jobs = sectionJobs(section, jobs);
  jobs.sort((a, b) => order.indexOf(statusOf(a)) - order.indexOf(statusOf(b)) || (b.score || 0) - (a.score || 0));
  const head = ["Status", "Applied on", "Score", "Title", "Company", "Location", "Remote", "Source", "Posted", "Found",
    "Matched skills", "Recruiter email", "Email draft", "Link"];
  const rows = jobs.map((j) => {
    const rec = store.statuses[j.id];
    const st = statusOf(j);
    return [isPending(j) ? "Pending" : sectionLabel(st), rec?.appliedAt && APPLIED.includes(st) ? rec.appliedAt.slice(0, 10) : "",
      j.score, j.title, j.company, j.location, j.remote ? "yes" : "no", SOURCE_LABELS[j.source] || j.source,
      (j.posted_date || "").slice(0, 10), (j.first_seen || "").slice(0, 10), j.matched_keywords,
      (j.contact_emails || [])[0] || j.outreach?.email, j.outreach?.draft_id ? (store.sent[j.id] ? "sent" : "drafted") : "", j.url];
  });
  // UTF-8 BOM so Excel shows ₹ and accents correctly
  const csv = "﻿" + [head, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
  download(`job-radar-${inSection ? section + "-" : ""}${new Date().toISOString().slice(0, 10)}.csv`, new Blob([csv], { type: "text/csv;charset=utf-8" }));
  toast(`Exported ${rows.length} jobs`);
}
function exportBackup() {
  download(`job-radar-backup-${new Date().toISOString().slice(0, 10)}.json`,
    new Blob([JSON.stringify({ app: "job-radar", version: 2, exported_at: new Date().toISOString(), ...store }, null, 1)], { type: "application/json" }));
}
async function importBackup(file) {
  try {
    const incoming = JSON.parse(await file.text());
    if (!incoming || typeof incoming.statuses !== "object") throw new Error("not a Job Radar backup");
    store = JRSync.merge(store, incoming);
    changed();
    render();
    toast("Backup imported");
  } catch (e) {
    toast(`Import failed: ${e.message}`);
  }
}

// -------------------------------------------------------------------- theme
const THEMES = ["auto", "light", "dark"];
const themeIcon = () => ({ auto: "🌓", light: "☀️", dark: "🌙" }[readJSON(K.theme, "auto")] || "🌓");
function applyTheme(theme) {
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = theme;
}
function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(readJSON(K.theme, "auto")) + 1) % THEMES.length];
  writeJSON(K.theme, next);
  applyTheme(next);
  toast(`Theme: ${next}`);
}

// --------------------------------------------------------------------- sync
let syncTimer = null;
let syncing = null;

function setSync(state, error = "") {
  syncInfo = { state, at: state === "ok" ? new Date().toISOString() : syncInfo.at, error };
  renderSyncBadges();
}
function syncLabel() {
  if (!session) return "This device only";
  if (syncInfo.state === "syncing") return "Syncing…";
  if (syncInfo.state === "offline") return "Offline · will sync later";
  if (syncInfo.state === "error") return `Sync problem: ${syncInfo.error}`;
  return syncInfo.at ? `Synced ${ago(syncInfo.at)}` : "Signed in";
}
function renderSyncBadges() {
  const state = session ? syncInfo.state : "off";
  $("#sync-dot").dataset.state = state;
  $("#sync-dot").title = syncLabel();
  $("#dr-sync").dataset.state = state;
  $(".txt", $("#dr-sync")).textContent = syncLabel();
}
function scheduleSync(delay = 1200) {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => syncNow(), delay);
}

// Read the shared file, merge it with this device, and write it back when this device has news
async function syncNow() {
  if (!session) return;
  if (syncing) return syncing;
  syncing = (async () => {
    if (!navigator.onLine) return setSync("offline");
    setSync("syncing");
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        const remote = await session.gh.get();
        const remoteState = remote ? await JRSync.open(remote.envelope, session.key) : JRSync.empty();
        store = JRSync.merge(store, remoteState);
        saveLocal();
        if (remote && !readJSON(K.dirty, false)) break; // nothing new here to send
        try {
          await session.gh.put(await JRSync.seal(store, session.key, session.salt, session.iter), remote?.sha);
          writeJSON(K.dirty, false);
          break;
        } catch (e) {
          if (e.status === 409 || e.status === 422) continue; // another device wrote first: merge again
          throw e;
        }
      }
      setSync("ok");
      render();
    } catch (e) {
      if (e.status === 401 || e.status === 403) setSync("error", "token expired or lacks access. Sign out and in again");
      else if (e.name === "OperationError") setSync("error", "passphrase doesn't match this file");
      else setSync(navigator.onLine ? "error" : "offline", e.message);
    }
  })();
  try {
    await syncing;
  } finally {
    syncing = null;
  }
}

async function startSession(cfg) {
  const key = typeof cfg.key === "string" ? await JRSync.importKey(cfg.key) : cfg.key;
  session = { ...cfg, key, gh: JRSync.client(cfg) };
}

// -------------------------------------------------------------------- login
function repoFromLocation() {
  const m = location.hostname.match(/^([\w-]+)\.github\.io$/i);
  const repo = location.pathname.split("/").filter(Boolean)[0];
  return m ? { owner: m[1], repo: repo || `${m[1]}.github.io` } : { owner: "", repo: "" };
}

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  const saved = readJSON(K.sync, null) || readJSON(K.sync, null, sessionStorage) || {};
  const guess = repoFromLocation();
  $("#lg-owner").value = saved.owner || guess.owner;
  $("#lg-repo").value = saved.repo || guess.repo;
  $("#lg-repo-label").textContent = `${$("#lg-owner").value || "?"}/${$("#lg-repo").value || "?"}`;
  $("#lg-repo-box").open = !$("#lg-owner").value;
  $("#lg-token-wrap").hidden = !!saved.token;
  $("#lg-confirm-wrap").hidden = true;
  $("#lg-sub").textContent = "Sign in to keep your applications in sync on every device.";
  $("#lg-error").textContent = "";
  $("#lg-pass").value = "";
  setTimeout(() => $("#lg-pass").focus(), 50);
}

async function onLogin(e) {
  e.preventDefault();
  const err = $("#lg-error");
  const submit = $("#lg-submit");
  err.textContent = "";
  const pass = $("#lg-pass").value;
  const saved = readJSON(K.sync, null) || readJSON(K.sync, null, sessionStorage) || {};
  const token = ($("#lg-token").value || saved.token || "").trim();
  const owner = $("#lg-owner").value.trim();
  const repo = $("#lg-repo").value.trim();
  if (pass.length < 8) return (err.textContent = "Use a passphrase of at least 8 characters.");
  if (!token) {
    $("#lg-token-wrap").hidden = false;
    return (err.textContent = "Paste your GitHub token (see “How do I get a token?”).");
  }
  if (!owner || !repo) {
    $("#lg-repo-box").open = true;
    return (err.textContent = "Fill in the repository owner and name.");
  }
  submit.disabled = true;
  submit.textContent = "Checking…";
  try {
    const info = await JRSync.client({ owner, repo, token, branch: "" }).repoInfo();
    if (info.permissions && !info.permissions.push) throw new Error("This token can read the repo but not write. Give it Contents: Read and write.");
    const branch = info.default_branch;
    const gh = JRSync.client({ owner, repo, token, branch });
    const remote = await gh.get();
    let key, salt, iter;
    if (remote) {
      ({ salt, iter } = remote.envelope);
      key = await JRSync.deriveKey(pass, salt, iter);
      try {
        await JRSync.open(remote.envelope, key);
      } catch {
        throw new Error("Wrong passphrase. Use the one you chose on your first device.");
      }
    } else {
      // First sign-in anywhere: choose the passphrase
      if ($("#lg-confirm-wrap").hidden) {
        $("#lg-confirm-wrap").hidden = false;
        $("#lg-sub").textContent = "First sign-in: choose a passphrase you'll use on every device, and type it again.";
        $("#lg-pass2").focus();
        throw Object.assign(new Error(""), { quiet: true });
      }
      if ($("#lg-pass2").value !== pass) throw new Error("The two passphrases don't match.");
      salt = JRSync.newSalt();
      iter = JRSync.ITERATIONS;
      key = await JRSync.deriveKey(pass, salt, iter);
    }
    const cfg = { owner, repo, branch, token, key: await JRSync.exportKey(key), salt, iter };
    const remember = $("#lg-remember").checked;
    writeJSON(K.sync, remember ? cfg : undefined);
    writeJSON(K.sync, remember ? undefined : cfg, sessionStorage);
    writeJSON(K.mode, undefined);
    await startSession(cfg);
    writeJSON(K.dirty, true); // bring this device's statuses into the shared file
    $("#lg-token").value = "";
    showApp();
    await syncNow();
    toast(remote ? "Signed in · statuses synced" : "Sync is on. Sign in on your other devices with the same passphrase.");
  } catch (ex) {
    if (!ex.quiet) err.textContent = ex.status === 401 ? "GitHub didn't accept that token. Check you copied all of it." : ex.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Sign in";
  }
}

function signOut() {
  if (!confirm("Sign out on this device? Your statuses stay in your encrypted sync file; this device forgets them until you sign in again.")) return;
  writeJSON(K.sync, undefined);
  writeJSON(K.sync, undefined, sessionStorage);
  writeJSON(K.mode, undefined);
  writeJSON(K.dirty, undefined);
  session = null;
  store = JRSync.empty();
  saveLocal();
  closeOverlay($("#nav"));
  closeOverlay($("#settings"));
  showLogin();
}

function showApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  animateNext = true;
  render();
}

// ------------------------------------------------------------------ settings
function openSettings() {
  renderSyncBody();
  renderAlerts();
  openOverlay($("#settings"));
}
function renderSyncBody() {
  const body = $("#sync-body");
  if (!session) {
    const b = el("button", { className: "btn btn-primary", type: "button", textContent: "Sign in to sync" });
    b.addEventListener("click", () => {
      closeOverlay($("#settings"));
      showLogin();
    });
    body.replaceChildren(el("p", { className: "muted", textContent: "Statuses are saved on this device only." }), b);
    return;
  }
  const now = el("button", { className: "btn btn-primary", type: "button", textContent: "Sync now" });
  now.addEventListener("click", async () => {
    await syncNow();
    renderSyncBody();
  });
  const out = el("button", { className: "btn", type: "button", textContent: "Sign out" });
  out.addEventListener("click", signOut);
  body.replaceChildren(
    el("p", { className: "muted", textContent: `${syncLabel()} · ${session.owner}/${session.repo} (encrypted)` }),
    el("div", { className: "row" }, now, out));
}

function b64uToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  return Uint8Array.from(atob((s + pad).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
}
function sameKey(a, b) {
  if (!a || !b) return false;
  const x = new Uint8Array(a), y = new Uint8Array(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

async function renderAlerts() {
  const body = $("#alerts-body");
  $("#alert-score").textContent = data.alert_score || 70;
  const note = (text) => body.replaceChildren(el("p", { className: "note", textContent: text }));
  if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
    return note(isIOS() && !isStandalone()
      ? "On iPhone, alerts work only from the installed app: Share → Add to Home Screen, then open Job Radar from the home screen."
      : "This browser doesn't support notifications. Try Chrome on Android, or the installed app on iPhone.");
  }
  const publicKey = data.push?.public_key;
  if (!publicKey) return note("Alerts aren't switched on yet. Add the VAPID_PRIVATE_KEY secret on GitHub (README: phone alerts), run the workflow once, then come back.");
  if (Notification.permission === "denied") return note("Notifications are blocked for this site. Allow them in the browser's site settings, then reopen Settings.");
  const reg = await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(r, 4000))]);
  if (!reg) return note("The app's background worker isn't running. Reload the page and try again.");
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options?.applicationServerKey, b64uToBytes(publicKey))) {
    await sub.unsubscribe();
    sub = null;
  }
  if (!sub) {
    const btn = el("button", { className: "btn btn-primary", type: "button", textContent: "Enable alerts on this device" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        if ((await Notification.requestPermission()) !== "granted") throw new Error("permission not granted");
        await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(publicKey) });
        renderAlerts();
      } catch (e) {
        toast(`Couldn't enable alerts: ${e.message}`);
        btn.disabled = false;
      }
    });
    return body.replaceChildren(btn);
  }
  const json = JSON.stringify(sub.toJSON());
  const box = el("textarea", { className: "sub-box", readOnly: true, value: json });
  const copy = el("button", { className: "btn btn-primary", type: "button", textContent: "Copy code" });
  copy.addEventListener("click", () => copyText(json, "Copied"));
  const off = el("button", { className: "btn", type: "button", textContent: "Turn off on this device" });
  off.addEventListener("click", async () => {
    await sub.unsubscribe();
    toast("Alerts turned off on this device");
    renderAlerts();
  });
  body.replaceChildren(
    el("p", { className: "note", textContent: "✓ This device is subscribed. One last step so GitHub knows where to send alerts:" }),
    el("ol", { className: "steps" },
      el("li", { textContent: "Copy the code below." }),
      el("li", { textContent: "GitHub → your repo → Settings → Secrets and variables → Actions." }),
      el("li", { textContent: "Create or edit WEBPUSH_SUBSCRIPTIONS and paste it. For several devices use a JSON list: [ {…}, {…} ]." }),
      el("li", { textContent: "Actions → Job Radar → Run workflow → test-notification." })),
    box, el("div", { className: "row" }, copy, off));
}

// ------------------------------------------------------------ pull to refresh
function bindPullToRefresh() {
  const ptr = $("#ptr");
  let y0 = null, dy = 0;
  window.addEventListener("touchstart", (e) => {
    y0 = window.scrollY <= 0 && !$("#app").hidden && !document.querySelector("dialog[open]") ? e.touches[0].clientY : null;
    dy = 0;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    dy = Math.max(0, e.touches[0].clientY - y0);
    ptr.style.height = `${Math.min(64, dy / 2)}px`;
    ptr.classList.toggle("armed", dy > 110);
  }, { passive: true });
  window.addEventListener("touchend", async () => {
    if (y0 === null) return;
    y0 = null;
    if (dy > 110) {
      ptr.classList.add("loading");
      ptr.style.height = "44px";
      await Promise.all([load(), syncNow()]);
      ptr.classList.remove("loading");
    }
    ptr.classList.remove("armed");
    ptr.style.height = "0";
  });
}

// --------------------------------------------------------------------- init
function bind() {
  $("#f-search").addEventListener("input", (e) => setFilters({ q: e.target.value.trim() }));
  $("#f-score").addEventListener("input", (e) => {
    $("#f-score-val").textContent = e.target.value;
    setFilters({ minScore: Number(e.target.value) });
  });
  $("#reset-filters").addEventListener("click", () => {
    filters = { ...DEFAULT_FILTERS };
    writeJSON(K.filters, filters);
    applyFiltersToInputs();
    render();
  });
  $("#toggle-filters").addEventListener("click", () => {
    const open = $("#toggle-filters").getAttribute("aria-expanded") !== "true";
    $("#toggle-filters").setAttribute("aria-expanded", String(open));
    $("#filter-panel").hidden = !open;
  });
  $("#refresh").addEventListener("click", () => {
    load();
    syncNow();
  });
  $("#open-nav").addEventListener("click", () => openOverlay($("#nav")));
  $("#section-title").addEventListener("click", () => openOverlay($("#nav")));
  $("#sync-dot").addEventListener("click", () => (session ? syncNow().then(() => toast(syncLabel())) : showLogin()));
  $("#dr-sync").addEventListener("click", () => {
    closeOverlay($("#nav"));
    session ? openSettings() : showLogin();
  });
  $("#dr-signout").addEventListener("click", () => {
    if (session) signOut();
    else {
      closeOverlay($("#nav"));
      showLogin();
    }
  });
  for (const d of document.querySelectorAll("dialog")) {
    d.addEventListener("close", () => onOverlayClosed(d));
    d.addEventListener("click", (e) => e.target === d && d.close()); // tap outside closes
  }
  for (const s of document.querySelectorAll(".stat")) s.addEventListener("click", () => setSection(s.dataset.go));
  for (const b of document.querySelectorAll("[data-export]")) b.addEventListener("click", () => exportCSV(b.dataset.export));
  $("#export").addEventListener("click", exportBackup);
  $("#import-file").addEventListener("change", (e) => {
    if (e.target.files[0]) importBackup(e.target.files[0]);
    e.target.value = "";
  });
  $("#login-form").addEventListener("submit", onLogin);
  for (const id of ["#lg-owner", "#lg-repo"]) $(id).addEventListener("input", () => ($("#lg-repo-label").textContent = `${$("#lg-owner").value}/${$("#lg-repo").value}`));
  $("#lg-skip").addEventListener("click", () => {
    writeJSON(K.mode, "local");
    showApp();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - lastFetch > 5 * 60e3) load({ quiet: true });
    syncNow();
  });
  window.addEventListener("online", () => syncNow());
  // Changes made on another device show up within a minute while the app is open
  setInterval(() => document.visibilityState === "visible" && syncNow(), 45e3);
  setInterval(() => document.visibilityState === "visible" && renderSyncBadges(), 30e3);
  window.addEventListener("storage", (e) => {
    if (e.key === K.store) {
      store = JRSync.normalize(readJSON(K.store, {}));
      render();
    }
  });
  window.addEventListener("hashchange", focusJobFromHash);
  bindPullToRefresh();
}

async function init() {
  applyTheme(readJSON(K.theme, "auto"));
  filters = normalizeFilters(readJSON(K.filters, {}));
  const savedSection = readJSON(K.section, "new");
  section = ALL_SECTIONS.some((x) => x.id === savedSection) ? savedSection : "new";
  history.replaceState({ s: section }, "", location.pathname + location.search + location.hash);
  bind();
  applyFiltersToInputs();
  $("#list").replaceChildren(...[0, 1, 2].map(() => el("div", { className: "skeleton" })));

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
    const hadController = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (hadController && !reloading) {
        reloading = true;
        location.reload();
      }
    });
  }

  const cfg = readJSON(K.sync, null) || readJSON(K.sync, null, sessionStorage);
  if (cfg?.key && cfg?.token) {
    try {
      await startSession(cfg);
    } catch {
      session = null;
    }
  }
  if (session || readJSON(K.mode, null) === "local") showApp();
  else showLogin();
  await load({ quiet: true });
  syncNow();
  focusJobFromHash();
}

init();
