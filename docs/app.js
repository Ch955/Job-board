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
  mode: "jobradar.mode",       // "local" = chose to use without sync
  dirty: "jobradar.dirty",     // unsynced local changes
  collapsed: "jobradar.collapsed", // which panels you folded, per browser
  phoneOnly: "jobradar.phoneOnly",
  sortNew: "jobradar.sortNew",     // "best" | "newest" in To apply
};
const APPLIED = ["applied", "interview", "offer", "rejected"];
const KEEP = ["later", "applied", "interview", "offer", "rejected"];
// An empty list means "all": All application types, All permitted sources, every location
const DEFAULT_FILTERS = { q: "", minScore: 40, location: [], source: [], remote: [], apply: [], band: [], status: [],
  skills: [], skillMode: "all", dateField: "found", datePreset: "any", dateFrom: "", dateTo: "" };
const MULTI = ["band", "status", "apply", "source", "location", "remote"];
const BANDS = [["strong", "Strong 85+"], ["good", "Good 70–84"], ["fair", "Fair 55–69"], ["low", "Low <55"]];
const bandOf = (job) => (job.score == null ? "none" : job.score >= 85 ? "strong" : job.score >= 70 ? "good" : job.score >= 55 ? "fair" : "low");
const DATE_PRESETS = [["any", "Any time"], ["1", "Today"], ["3", "Last 3 days"], ["7", "Last 7 days"], ["14", "Last 14 days"],
  ["30", "Last 30 days"], ["custom", "Custom range…"]];
const APPLY_TYPES = [["easy_apply", "Easy Apply"], ["company_site", "Company website"], ["external", "External application"],
  ["unknown", "Application type unknown"]];
const ALL_LABEL = { apply: "All application types", source: "All sources", location: "All locations", remote: "Any work mode",
  band: "Any fit", status: "All statuses" };
// One colour per job source, used on filter chips and job cards
const SOURCE_COLORS = { naukri: "#4a7bd0", linkedin: "#0a66c2", indeed: "#2557a7", glassdoor: "#0caa41", google: "#ea4335",
  greenhouse: "#24a47f", lever: "#6b5bd2", ashby: "#c2410c", smartrecruiters: "#0e7490", adzuna: "#b45309", jooble: "#be185d",
  remotive: "#0d9488", arbeitnow: "#7c3aed", email: "#64748b" };
const sourceColor = (s) => SOURCE_COLORS[s] || "#8a8f98";
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
const PANEL_SECTIONS = [
  { id: "pipeline", label: "Pipeline", icon: "📋", tile: "var(--primary)" },
  { id: "skills", label: "Skill match", icon: "🧩", tile: "var(--good)" },
];
const STAGE_ORDER = ["offer", "interview", "applied", "new", "later", "rejected", "skipped"];
const WALKIN_SECTION = { id: "walkins", label: "Walk-in interviews", icon: "🚶", tile: "var(--s-mid)" };
const GMAIL_SECTIONS = [
  { id: "mail", label: "Recruiter emails", icon: "✉️", tile: "var(--primary)" },
  { id: "reply", label: "Needs your reply", icon: "📥", tile: "var(--interview)" },
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
const ALL_SECTIONS = [...SECTIONS, ...PANEL_SECTIONS, WALKIN_SECTION, ...GMAIL_SECTIONS];
const JOB_SECTIONS = SECTIONS.map((x) => x.id).concat("pipeline"); // sections the job filters apply to
const sectionLabel = (id) => ALL_SECTIONS.find((x) => x.id === id)?.label || id;

const $ = (sel, root = document) => root.querySelector(sel);

let data = { jobs: [] };
let filters = { ...DEFAULT_FILTERS };
let section = "new";
let lastFetch = 0;
let animateNext = true;
let reloading = false;
let session = null;  // {uid, email, name, photo} when signed in
let appConfig = {};  // docs/app-config.json: Firebase settings (public by design)
let syncInfo = { state: "off", at: null, error: "" };
let readOnly = "";    // why changes can't be saved (no write access), or ""
let loadFailed = false;
const showAll = {};   // sections where you tapped "Show all"
const PAGE = 25;

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

function setStatus(job, status, extra = {}) {
  const prev = store.statuses[job.id];
  const now = new Date().toISOString();
  const rec = { status, at: now, ...extra };
  const appliedAt = prev?.appliedAt || (APPLIED.includes(status) ? now : null);
  if (appliedAt) rec.appliedAt = appliedAt;
  store.statuses[job.id] = rec; // "new" is a record too, so an undo syncs to other devices
  if (KEEP.includes(status)) store.saved[job.id] = snapshot(job);
  else delete store.saved[job.id];
  if (store.pending[job.id]?.open) store.pending[job.id] = { at: now, open: false };
  changed();
}

function changed() {
  if (readOnly) toast(`Not saved: ${readOnly}`);
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
    if (JRAccount.enabled()) {
      data = await JRAccount.loadMatches(); // your own matches, scored from your profile
    } else {
      const r = await fetch(`jobs.json?t=${Date.now()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    }
    data.jobs = Array.isArray(data.jobs) ? data.jobs : [];
    lastFetch = Date.now();
    loadFailed = false;
    if (!quiet) toast(`Updated · ${data.jobs.length} jobs`);
  } catch {
    loadFailed = true;
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

// Same rules as jobradar/apply_type.py, for jobs saved before the server recorded the type
const ATS_HOSTS = ["greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "workday.com",
  "icims.com", "taleo.net", "successfactors.com", "jobvite.com", "zohorecruit.com", "recruitee.com", "workable.com",
  "bamboohr.com", "darwinbox.in", "keka.com", "freshteam.com", "oraclecloud.com", "breezy.hr", "teamtailor.com"];
const EMPLOYER_BOARDS = ["greenhouse", "lever", "ashby", "smartrecruiters"];
const AGGREGATOR_SOURCES = ["adzuna", "jooble", "remotive", "arbeitnow"];
function applyTypeOf(job) {
  if (APPLY_TYPES.some(([t]) => t === job.apply_type)) return job.apply_type;
  let host = "";
  try { host = new URL(job.url).hostname.toLowerCase(); } catch { return "unknown"; }
  if (EMPLOYER_BOARDS.includes(job.source) || ATS_HOSTS.some((d) => host === d || host.endsWith("." + d))) return "company_site";
  if (AGGREGATOR_SOURCES.includes(job.source)) return "external";
  return "unknown";
}
const applyLabel = (t) => (APPLY_TYPES.find(([k]) => k === t) || APPLY_TYPES[3])[1];
const FIELD_VALUE = { source: (j) => j.source, location: locationLabel, remote: modeOf, apply: applyTypeOf, band: bandOf,
  status: (j) => statusOf(j) };
const skillsOf = (job) => [...new Set([...(job.matched_keywords || []), ...(job.match?.missing_skills || [])])];
// Found date or (for jobs you applied to) applied date, as a Date or null
function dateOf(job, field) {
  const iso = field === "applied" ? store.statuses[job.id]?.appliedAt : job.first_seen || job.posted_date;
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? d : null;
}
function dateRange(f = filters) {
  if (f.datePreset === "any") return null;
  if (f.datePreset === "custom") {
    const from = f.dateFrom ? new Date(f.dateFrom + "T00:00:00") : null;
    const to = f.dateTo ? new Date(f.dateTo + "T23:59:59") : null;
    return from || to ? [from, to] : null;
  }
  const from = startOfToday();
  from.setDate(from.getDate() - (Number(f.datePreset) - 1));
  return [from, null];
}
function inDateRange(job, f = filters) {
  const r = dateRange(f);
  if (!r) return true;
  const d = dateOf(job, f.dateField);
  return !!d && (!r[0] || d >= r[0]) && (!r[1] || d <= r[1]);
}

// ------------------------------------------------------------------ filters
function matchesFilters(job, ignore) {
  if (ignore !== "minScore" && (job.score ?? 100) < filters.minScore) return false;
  for (const k of MULTI) {
    if (k === "status" && section !== "pipeline") continue; // the other sections are a status already
    if (k !== ignore && filters[k].length && !filters[k].includes(FIELD_VALUE[k](job))) return false;
  }
  if (ignore !== "date" && !inDateRange(job)) return false;
  if (ignore !== "skills" && filters.skills.length) {
    const have = skillsOf(job).map((x) => x.toLowerCase());
    const hit = (s) => have.includes(s.toLowerCase());
    if (filters.skillMode === "any" ? !filters.skills.some(hit) : !filters.skills.every(hit)) return false;
  }
  if (filters.q) {
    const hay = `${job.title} ${job.company} ${job.location} ${locationLabel(job)}`.toLowerCase();
    if (!filters.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}
function normalizeFilters(f) {
  const out = { ...DEFAULT_FILTERS, ...f };
  for (const k of [...MULTI, "skills"]) {
    const v = out[k];
    out[k] = Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];
  }
  if (!["all", "any"].includes(out.skillMode)) out.skillMode = "all";
  if (!DATE_PRESETS.some(([v]) => v === out.datePreset)) out.datePreset = "any";
  return out;
}
// Filter choices are kept per account on this device (and for "this device only" use)
const filterKey = () => (session?.uid ? `${K.filters}.${session.uid}` : K.filters);
function loadFilters() {
  filters = normalizeFilters(readJSON(filterKey(), null) ?? readJSON(K.filters, {}));
}
function setFilters(patch) {
  filters = { ...filters, ...patch };
  writeJSON(filterKey(), filters);
  render();
}
const activeFilters = () => {
  const out = [];
  if (filters.q) out.push(["q", `“${filters.q}”`]);
  if (filters.minScore !== DEFAULT_FILTERS.minScore) out.push(["minScore", `Fit ≥ ${filters.minScore}`]);
  const names = { band: (v) => BANDS.find(([k]) => k === v)?.[1] || v, status: sectionLabel, apply: applyLabel,
    source: (v) => SOURCE_LABELS[v] || v, location: (v) => v, remote: (v) => WORK_MODES.find(([k]) => k === v)?.[1] || v };
  for (const k of MULTI) {
    if (filters[k].length && !(k === "status" && section !== "pipeline")) out.push([k, filters[k].map(names[k]).join(", ")]);
  }
  if (dateRange()) {
    const label = filters.datePreset === "custom" ? `${filters.dateFrom || "…"} – ${filters.dateTo || "…"}`
      : DATE_PRESETS.find(([v]) => v === filters.datePreset)[1].toLowerCase();
    out.push(["date", `${filters.dateField === "applied" ? "Applied" : "Found"} ${label}`]);
  }
  if (filters.skills.length) out.push(["skills", `${filters.skillMode === "any" ? "Any of" : "All of"}: ${filters.skills.join(", ")}`]);
  return out;
};
const activeFilterCount = () => activeFilters().filter(([k]) => k !== "q").length;
function clearFilters() {
  filters = { ...DEFAULT_FILTERS };
  writeJSON(filterKey(), filters);
  applyFiltersToInputs();
  render();
  toast("Filters cleared");
}

function buildFilterChips() {
  const all = allJobs();
  const inSection = section === "pipeline" ? all : all.filter((j) => statusOf(j) === section);
  // Sources: every enabled, permitted source, plus any your own saved jobs came from
  const available = data.sources_available || [];
  const groups = {
    band: BANDS,
    status: STAGE_ORDER.filter((st) => all.some((j) => statusOf(j) === st)).map((st) => [st, sectionLabel(st)]),
    apply: APPLY_TYPES,
    source: [...new Set([...available, ...all.map((j) => j.source)])].sort()
      .map((v) => [v, (SOURCE_LABELS[v] || v) + (available.length && !available.includes(v) && v !== "email" ? " (saved only)" : "")]),
    location: [...new Set(all.map(locationLabel))].sort().map((v) => [v, v]),
    remote: WORK_MODES,
  };
  const focused = document.activeElement?.dataset?.chip;
  for (const key of MULTI) {
    const base = inSection.filter((j) => matchesFilters(j, key));
    const allOn = !filters[key].length;
    const allChip = el("button", { type: "button", className: "chip chip-all" }, ALL_LABEL[key],
      el("span", { className: "chip-count", textContent: base.length }));
    allChip.dataset.chip = `${key}:*`;
    allChip.setAttribute("aria-pressed", String(allOn));
    allChip.addEventListener("click", () => setFilters({ [key]: [] }));
    $(`#f-${key}`).replaceChildren(allChip, ...groups[key].map(([value, label]) => {
      const on = filters[key].includes(value);
      const b = el("button", { type: "button", className: "chip" },
        key === "source" ? el("span", { className: "src-dot", style: `background:${sourceColor(value)}` }) : null, label,
        el("span", { className: "chip-count", textContent: base.filter((j) => FIELD_VALUE[key](j) === value).length }));
      b.dataset.chip = `${key}:${value}`;
      b.setAttribute("aria-pressed", String(on));
      b.addEventListener("click", () => setFilters({ [key]: on ? filters[key].filter((x) => x !== value) : [...filters[key], value] }));
      return b;
    }));
  }
  $("#status-field").hidden = section !== "pipeline";

  // Skills: the ones these jobs mention, most common first; counts given every other filter
  const skillBase = inSection.filter((j) => matchesFilters(j, "skills"));
  const freq = new Map();
  for (const j of skillBase) for (const k of skillsOf(j)) freq.set(k, (freq.get(k) || 0) + 1);
  const picked = filters.skills.filter((k) => !freq.has(k));
  const skillList = [...picked.map((k) => [k, 0]), ...[...freq].sort((a, b) => b[1] - a[1]).slice(0, 30)];
  $("#f-skills").replaceChildren(...skillList.map(([k]) => {
    const on = filters.skills.includes(k);
    const would = on ? skillBase.filter((j) => matchesFilters(j)).length
      : skillBase.filter((j) => {
          const have = skillsOf(j).map((x) => x.toLowerCase());
          const sel = [...filters.skills, k];
          return filters.skillMode === "any" ? sel.some((x) => have.includes(x.toLowerCase())) : sel.every((x) => have.includes(x.toLowerCase()));
        }).length;
    const b = el("button", { type: "button", className: "chip" }, k, el("span", { className: "chip-count", textContent: would }));
    b.dataset.chip = `skills:${k}`;
    b.setAttribute("aria-pressed", String(on));
    b.addEventListener("click", () => setFilters({ skills: on ? filters.skills.filter((x) => x !== k) : [...filters.skills, k] }));
    return b;
  }));
  if (!skillList.length) $("#f-skills").replaceChildren(el("span", { className: "muted", textContent: "No skills listed on these jobs." }));
  $("#f-skills-all").setAttribute("aria-pressed", String(filters.skillMode === "all"));
  $("#f-skills-any").setAttribute("aria-pressed", String(filters.skillMode === "any"));

  // Date presets, each with the number of jobs it would show
  const dateBase = inSection.filter((j) => matchesFilters(j, "date"));
  const sel = $("#f-date-preset");
  sel.replaceChildren(...DATE_PRESETS.map(([v, label]) => {
    const n = v === "custom" ? null : dateBase.filter((j) => inDateRange(j, { ...filters, datePreset: v })).length;
    return el("option", { value: v, textContent: n == null ? label : `${label} (${n})`, selected: filters.datePreset === v });
  }));
  $("#f-date-field").value = filters.dateField;
  $("#date-custom").hidden = filters.datePreset !== "custom";
  $("#f-date-from").value = filters.dateFrom;
  $("#f-date-to").value = filters.dateTo;
  if (focused) document.querySelector(`[data-chip="${CSS.escape(focused)}"]`)?.focus();
}

function renderFilterSummary(shown, total) {
  const box = $("#filter-summary");
  const act = activeFilters();
  box.hidden = !act.length || !JOB_SECTIONS.includes(section);
  if (box.hidden) return;
  const clear = el("button", { type: "button", className: "link-btn", textContent: "Clear all" });
  clear.addEventListener("click", clearFilters);
  box.replaceChildren(el("b", { textContent: `${shown} of ${total} jobs` }),
    ...act.map(([, label]) => el("span", { className: "fs-chip", textContent: label })), clear);
}
function applyFiltersToInputs() {
  $("#f-search").value = filters.q;
  $("#f-score").value = filters.minScore;
  $("#f-score-val").textContent = filters.minScore;
}

// ------------------------------------------------------------------- render
function sectionJobs(id, jobs = allJobs()) {
  return jobs.filter((j) => (id === "pipeline" || statusOf(j) === id) && matchesFilters(j));
}
const byFit = (a, b) => (b.score || 0) - (a.score || 0);
const foundAt = (j) => String(j.first_seen || j.posted_date || "");
function sortFor(id) {
  const at = (j) => store.statuses[j.id]?.at || "";
  if (id === "applied") return (a, b) => (store.statuses[b.id]?.appliedAt || "").localeCompare(store.statuses[a.id]?.appliedAt || "");
  if (id === "later") return (a, b) => String(a.company).localeCompare(String(b.company), undefined, { sensitivity: "base" }) || byFit(a, b);
  if (["interview", "offer", "rejected", "skipped"].includes(id)) return (a, b) => at(b).localeCompare(at(a));
  if (readJSON(K.sortNew, "best") === "newest") return (a, b) => foundAt(b).localeCompare(foundAt(a)) || byFit(a, b);
  // Best order: jobs you opened but didn't confirm, then quick (phone-friendly) applies, then the rest; each by fit
  return (a, b) => (isPending(b) - isPending(a)) || ((JRDesk.deviceOf(b) === "phone") - (JRDesk.deviceOf(a) === "phone")) ||
    byFit(a, b) || foundAt(b).localeCompare(foundAt(a));
}

function appliedTimes() {
  return Object.values(store.statuses).filter((s) => APPLIED.includes(s.status) && s.appliedAt).map((s) => new Date(s.appliedAt));
}
const fmtDateTime = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "");

function renderHeader(jobs) {
  for (const st of ["new", "later", "applied", "interview", "offer", "rejected"]) {
    countUp($(`#st-${st}`), jobs.filter((j) => statusOf(j) === st).length);
  }
  for (const b of document.querySelectorAll(".stage")) b.setAttribute("aria-current", String(b.dataset.go === section));
  const weekAgo = Date.now() - 7 * 86400e3;
  const n7 = appliedTimes().filter((d) => d.getTime() >= weekAgo).length;
  const runAt = data.run?.at || data.generated_at;
  $("#runinfo").textContent = `${runAt ? `Last run ${fmtDateTime(runAt)}` : "No run yet"} · ${n7} applied in the last 7 days`;
  const failed = JRDesk.failedSources();
  const warn = $("#src-warn");
  warn.hidden = !failed.length;
  if (failed.length) {
    warn.textContent = `⚠ ${failed.length} source${failed.length > 1 ? "s" : ""} failed`;
    warn.title = failed.map((f) => `${SOURCE_LABELS[f.source] || f.source}: ${f.reason}`).join("\n");
  }
  const offline = !navigator.onLine || loadFailed;
  $("#net-banner").hidden = !offline;
  $("#net-banner").textContent = offline ? `Offline: can't reach the job data, so you're seeing what this device saved${data.generated_at ? ` (${ago(data.generated_at)})` : ""}. Your changes are kept and sync when you're back online.` : "";
  $("#ro-banner").hidden = !readOnly;
  $("#ro-banner").textContent = readOnly ? `Read-only: ${readOnly}. Buttons that change things are turned off.` : "";
  document.body.classList.toggle("read-only", !!readOnly);
}

function render() {
  const jobs = allJobs();
  const cards = SECTIONS.some((x) => x.id === section);
  const filtered = JOB_SECTIONS.includes(section);
  const all = cards ? sectionJobs(section, jobs).sort(sortFor(section)) : [];
  const phoneCount = section === "new" ? all.filter((j) => JRDesk.deviceOf(j) === "phone").length : 0;
  const phoneOnly = section === "new" && readJSON(K.phoneOnly, false);
  const shown = phoneOnly ? all.filter((j) => JRDesk.deviceOf(j) === "phone") : all;
  const list = $("#list");

  $("#section-name").textContent = sectionLabel(section);
  const multiCount = JRAccount.enabled();
  $("#section-count").textContent = cards ? shown.length : section === "pipeline" ? sectionJobs("pipeline", jobs).length
    : section === "mail" && multiCount ? JRAccount.mailCount() : section === "reply" && multiCount ? JRAccount.inboxOpen()
    : section === "skills" && multiCount ? JRDesk.missingSkills().length : "–";
  $("#section-hint").textContent = SECTIONS.find((x) => x.id === section)?.hint || "";
  $(".filter-row").hidden = !filtered;
  $("#filter-panel").hidden = !filtered || $("#toggle-filters").getAttribute("aria-expanded") !== "true";
  document.title = `${sectionLabel(section)} · Job Radar`;

  const multi = JRAccount.enabled();
  const top = section === "new" ? JRDesk.applyBar(phoneCount, all.length) : [];
  if (!cards) {
    list.replaceChildren(section === "pipeline" ? JRDesk.renderPipeline(jobs)
      : section === "skills" ? JRDesk.renderSkills()
      : section === "walkins" ? (multi ? JRAccount.renderWalkins()
        : el("div", { className: "empty" }, el("b", { textContent: "Sign in to see walk-ins" }), "Walk-ins use your own cities and profile."))
      : section === "mail" ? (multi ? JRAccount.renderDrafts() : renderMailSection())
      : multi ? JRAccount.renderInbox() : renderInboxSection());
  } else if (!shown.length) {
    const any = jobs.some((j) => statusOf(j) === section);
    list.replaceChildren(...top, el("div", { className: "empty" },
      el("b", { textContent: phoneOnly && all.length ? "No phone-friendly jobs here" : any ? "No jobs match these filters" : !data.jobs.length && section === "new" ? "No jobs yet" : `Nothing in ${sectionLabel(section)} yet` }),
      phoneOnly && all.length ? `Turn off “Phone only” to see the ${all.length} laptop jobs.`
        : any ? "Try lowering the minimum fit or clearing filters."
        : section === "new" && multi && JRAccount.me?.missing?.length ? "Add your resume or profile (Settings → Your account) and your matches appear here."
        : section === "new" && data.searching ? "Searching for jobs that fit your profile. This usually takes a few minutes; matches appear here on their own."
        : section === "new" && multi ? `Nothing at or above your threshold (${JRAccount.profile().threshold ?? 70}) yet. New jobs are checked every 3 hours; you can lower the threshold in your profile.`
        : section === "new" ? "The next run (every 3 hours) fills this in."
        : section === "applied" ? "Tap Applied on a job and it moves here." : ""));
  } else {
    const limit = showAll[section] ? shown.length : PAGE;
    const more = shown.length > limit ? el("button", { className: "btn show-all", type: "button", textContent: `Show all ${shown.length} jobs` }) : null;
    if (more) {
      more.dataset.safe = "1";
      more.addEventListener("click", () => {
        showAll[section] = true;
        render();
      });
    }
    list.replaceChildren(...top, ...shown.slice(0, limit).map((job, i) => renderCard(job, i)), ...(more ? [more] : []));
  }
  animateNext = false;
  if (readOnly) for (const b of list.querySelectorAll("button:not([data-safe])")) b.disabled = true;

  renderHeader(jobs);
  $("#updated").textContent = data.searching ? `Searching for new jobs… started ${ago(new Date(data.search_started * 1000).toISOString())}`
    : data.generated_at ? `Jobs updated ${ago(data.generated_at)} · ${data.jobs.length} found` : "Waiting for the first run";

  const n = activeFilterCount();
  $("#toggle-filters").textContent = n ? `Filters (${n})` : "Filters";
  $("#toggle-filters").classList.toggle("has-filters", n > 0);
  if (filtered) {
    buildFilterChips();
    const base = section === "pipeline" ? jobs : jobs.filter((j) => statusOf(j) === section);
    renderFilterSummary(section === "pipeline" ? sectionJobs("pipeline", jobs).length : shown.length, base.length);
  } else {
    $("#filter-summary").hidden = true;
  }
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
  const at = applyTypeOf(job);
  const ab = el("span", { className: `badge atype atype-${at}`, textContent: applyLabel(at) });
  ab.title = job.apply_evidence || (at === "unknown" ? "How to apply isn't shown in the listing" : "From the job's link");
  meta.append(ab);
  if (job.work_mode === "hybrid") meta.append(el("span", { className: "badge", textContent: "hybrid" }));
  if (["new", "later"].includes(status)) {
    const dev = status === "later" ? "laptop" : JRDesk.deviceOf(job);
    const db = el("span", { className: `badge device device-${dev}`, textContent: dev === "phone" ? "📱 Phone OK" : "💻 Laptop" });
    db.title = JRDesk.deviceWhy(job, status);
    meta.append(db);
  }
  const yrs = job.score_breakdown?.years_required;
  for (const p of [locationLabel(job), job.remote ? "Remote" : null]) if (p) meta.append(el("span", { textContent: p }));
  meta.append(el("span", { className: "src" }, el("span", { className: "src-dot", style: `background:${sourceColor(job.source)}` }),
    SOURCE_LABELS[job.source] || job.source));
  meta.append(el("span", { textContent: job.posted_date ? `posted ${ago(job.posted_date)}` : `found ${ago(job.first_seen)}` }));
  if (yrs != null) meta.append(el("span", { textContent: `${yrs}${job.score_breakdown.years_max ? `–${job.score_breakdown.years_max}` : "+"} yrs` }));

  // Why it fits / the gap, in one line each; parked reason; another job at this company parked
  const [why, gap] = JRDesk.fitLines(job);
  if (why) Object.assign($(".why-line", card), { hidden: false, textContent: why });
  if (gap) Object.assign($(".gap-line", card), { hidden: false, textContent: gap });
  const warn = $(".warn-line", card);
  if (status === "later" && rec) {
    Object.assign(warn, { hidden: false, className: "warn-line parked", textContent: `Parked ${fmtDate(rec.at)}${rec.reason ? ` · ${rec.reason}` : ""}` });
  } else if (status === "new") {
    const co = JRDesk.companyKey(job.company);
    const other = allJobs().find((j) => j.id !== job.id && statusOf(j) === "later" && JRDesk.companyKey(j.company) === co);
    if (other) Object.assign(warn, { hidden: false, textContent: `⚠ You parked another ${job.company} job in Apply later: ${other.title}` });
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
  const url = safeUrl(job.apply_url || job.url);
  apply.textContent = applied ? "View job" : pending ? "⏳ Pending" : "Apply";
  apply.title = at === "company_site" ? "Opens the employer's careers site" : at === "external" ? "Opens the listing on another site" : "Opens the job listing";
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
    const btn = el("button", { className: `btn${to === "applied" ? " go-applied" : ""}`, type: "button",
      textContent: to === "skipped" && status === "new" ? "Not interested" : label });
    btn.dataset.to = to;
    btn.addEventListener("click", () => (to === "later" ? JRDesk.askLater(job, card) : moveJob(job, to, card)));
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
  if (["new", "later"].includes(status)) {
    const copy = el("button", { className: "mini-btn", type: "button", textContent: "📋 Copy answers", title: "Copy your standard form answers for this job" });
    copy.dataset.safe = "1";
    copy.addEventListener("click", () => JRDesk.copyAnswers(job));
    $(".outreach-row", card).append(copy);
  }
  renderOutreach(job, status, $(".outreach-row", card));
  JRAccount.decorateCard(job, card);
  return card;
}

function moveJob(job, to, card, extra) {
  const before = statusOf(job);
  const prevRec = store.statuses[job.id] ? { ...store.statuses[job.id] } : null;
  const prevSaved = store.saved[job.id];
  const prevPending = store.pending[job.id] ? { ...store.pending[job.id] } : null;
  const finish = () => {
    setStatus(job, to, extra);
    render();
    toast(to === "new" ? "Back in To apply" : to === "applied" ? `Marked applied · ${fmtDate(new Date().toISOString())}`
      : `Moved to ${sectionLabel(to)}${extra?.reason ? ` · ${extra.reason}` : ""}`, {
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
    if (!matchesFilters(j)) continue;
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
    item(PANEL_SECTIONS[0], jobs.filter((j) => matchesFilters(j)).length),
    item(PANEL_SECTIONS[1], JRAccount.enabled() ? JRDesk.missingSkills().length : null),
    ...(JRAccount.enabled() ? [item(WALKIN_SECTION, null)] : []),
    el("p", { className: "nav-group", textContent: "Gmail" }),
    item(GMAIL_SECTIONS[0], JRAccount.enabled() ? JRAccount.mailCount() : jobs.filter((j) => j.outreach?.draft_id).length),
    item(GMAIL_SECTIONS[1], JRAccount.enabled() ? JRAccount.inboxOpen() : (store.gmail.events || []).length),
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
  const av = $("#dr-avatar");
  if (session?.photo) {
    av.textContent = "";
    av.style.backgroundImage = `url("${encodeURI(session.photo)}")`;
    av.classList.add("has-img");
  } else {
    av.style.backgroundImage = "";
    av.classList.remove("has-img");
    av.textContent = name.split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "JR";
  }
  $("#dr-name").textContent = p.name || name;
  $("#dr-role").textContent = [p.role, p.years_experience ? `${p.years_experience}+ yrs` : "", p.notice].filter(Boolean).join(" · ");
  if (session?.email) $("#dr-role").textContent += ` · ${session.email}`;
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
    skills: skills.join(", "), // only skills that are both in the posting and yours
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
  if (JRAccount.enabled()) return; // drafts come from your own Gmail connection (account.js)
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
    wrap.append(el("p", { className: "note", textContent: "Sign in first: Gmail updates are saved to your account." }));
  } else if (!g.last_check) {
    wrap.append(el("p", { className: "note", textContent: "Not connected to Gmail yet. Finish the Gmail setup in the README (GMAIL_* and FIREBASE_SERVICE_ACCOUNT secrets). Then confirmations, interview invites and rejections update your jobs every 3 hours." }));
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
// session = {uid, email, name, photo} once signed in. Statuses live in Firestore
// users/<uid> (cloud.js); every device listens to it, and saves merge in a transaction.
let syncTimer = null;
let saving = null;
let unsubscribe = null;
let localVersion = 0;
let freshSignIn = false;

function setSync(state, error = "") {
  syncInfo = { state, at: state === "ok" ? new Date().toISOString() : syncInfo.at, error };
  renderSyncBadges();
}
function syncLabel() {
  if (!session) return "This device only";
  if (syncInfo.state === "syncing") return "Saving…";
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
function scheduleSync(delay = 700) {
  localVersion++;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(() => syncNow(), delay);
}
function cloudError(e) {
  const code = e?.code || "";
  if (code.includes("permission-denied")) return "no access. Check the Firestore rules in the README";
  if (code.includes("unavailable")) return "can't reach the server";
  return e?.message || String(e);
}

// Send this device's changes; the live listener brings in everyone else's
async function syncNow() {
  if (!session || !window.JRCloud?.ready) return;
  if (saving) return saving;
  if (!readJSON(K.dirty, false)) return;
  saving = (async () => {
    if (!navigator.onLine) return setSync("offline");
    setSync("syncing");
    const v = localVersion;
    try {
      const local = store;
      const merged = await JRCloud.save(session.uid, session.email, (remote) => JRSync.merge(local, remote));
      store = JRSync.merge(store, merged); // keep anything you changed while it was saving
      saveLocal();
      if (v === localVersion) writeJSON(K.dirty, false);
      readOnly = "";
      setSync("ok");
      render();
    } catch (e) {
      if (String(e?.code || "").includes("permission-denied")) readOnly = "this account can't save changes here (no write access)";
      setSync(navigator.onLine ? "error" : "offline", cloudError(e));
      render();
    }
  })();
  try {
    await saving;
  } finally {
    saving = null;
    if (readJSON(K.dirty, false) && syncInfo.state === "ok") scheduleSync(); // changed again meanwhile
  }
}

function listen() {
  unsubscribe?.();
  unsubscribe = JRCloud.subscribe(session.uid, (remote, mine) => {
    if (mine || !remote) return;
    store = JRSync.merge(store, remote);
    saveLocal();
    setSync("ok");
    render();
  }, (e) => {
    if (String(e?.code || "").includes("permission-denied")) readOnly = "this account can't read or save its data here (no access)";
    setSync("error", cloudError(e));
    render();
  });
}

function onUserChanged(user) {
  readOnly = "";
  if (user) {
    session = user;
    loadFilters();
    applyFiltersToInputs();
    writeJSON("jobradar.user", user);
    writeJSON(K.mode, undefined);
    if (freshSignIn) {
      writeJSON(K.dirty, true); // bring this device's statuses into your account
      freshSignIn = false;
    }
    setSync("ok");
    listen();
    showApp();
    syncNow();
    JRAccount.onSignedIn();
  } else {
    JRAccount.signedOut();
    unsubscribe?.();
    unsubscribe = null;
    session = null;
    writeJSON("jobradar.user", undefined);
    if (readJSON(K.mode, null) === "local") showApp();
    else showLogin();
  }
}

// -------------------------------------------------------------------- login
const firebaseCfg = () => appConfig.firebase || {};
const cloudConfigured = () => !!(firebaseCfg().apiKey && firebaseCfg().projectId);
let loginMode = "signin"; // or "signup"

function authMessage(e) {
  const code = e?.code || "";
  return ({
    "auth/invalid-credential": "Wrong email or password.",
    "auth/invalid-login-credentials": "Wrong email or password.",
    "auth/wrong-password": "Wrong email or password.",
    "auth/user-not-found": "No account with that email. Tap Create an account.",
    "auth/email-already-in-use": "That email already has an account. Sign in instead.",
    "auth/weak-password": "Use a password of at least 6 characters.",
    "auth/invalid-email": "That email address doesn't look right.",
    "auth/missing-email": "Enter your email first.",
    "auth/too-many-requests": "Too many tries. Wait a minute, or reset your password.",
    "auth/network-request-failed": "No internet connection.",
    "auth/popup-closed-by-user": "",
    "auth/cancelled-popup-request": "",
    "auth/popup-blocked": "Your browser blocked the Google window. Allow pop-ups for this site (icon at the right of the address bar), then try again.",
    "auth/web-storage-unsupported": "This browser is blocking the storage Google sign-in needs (often in private/incognito windows). Use a normal window, or sign in with email and password.",
    "auth/internal-error": "Google sign-in failed inside Firebase. Try again; if it repeats, use email and password and tell the app owner.",
    "auth/unauthorized-domain": "This site isn't allowed in Firebase yet: Authentication → Settings → Authorized domains → add it.",
    "auth/operation-not-allowed": "Turn this sign-in method on in Firebase → Authentication → Sign-in method.",
  })[code] ?? `${e?.message || "Sign-in failed."}${code ? ` (${code})` : ""}`;
}

function setLoginMode(mode) {
  loginMode = mode;
  const up = mode === "signup";
  $("#lg-confirm-wrap").hidden = !up;
  $("#lg-forgot").hidden = up;
  $("#lg-pass").autocomplete = up ? "new-password" : "current-password";
  $("#lg-submit").textContent = up ? "Create account" : "Sign in";
  $("#lg-switch-text").textContent = up ? "Already have an account?" : "New here?";
  $("#lg-mode").textContent = up ? "Sign in" : "Create an account";
  $("#lg-sub").textContent = up
    ? "Create your account. Use the same email on your phone and laptop."
    : "Sign in to keep your applications in sync on every device.";
  $("#lg-error").textContent = "";
  $("#lg-info").textContent = "";
}

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  const ready = cloudConfigured();
  for (const sel of ["#lg-google", "#lg-email", "#lg-pass", "#lg-pass2", "#lg-submit", "#lg-forgot", "#lg-mode"]) $(sel).disabled = !ready;
  $("#lg-setup-note").hidden = ready;
  $("#lg-setup-note").textContent = ready ? "" : "Sign-in isn't set up for this app yet (README: Sign in with email). You can use it on this device meanwhile.";
  const last = readJSON("jobradar.lastEmail", "");
  if (last && !$("#lg-email").value) $("#lg-email").value = last;
  $("#lg-pass").value = "";
  $("#lg-pass2").value = "";
  setLoginMode(loginMode);
  if (ready) setTimeout(() => ($("#lg-email").value ? $("#lg-pass") : $("#lg-email")).focus(), 50);
}

async function withBusy(button, label, fn) {
  const old = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}

async function onLogin(e) {
  e.preventDefault();
  const err = $("#lg-error");
  err.textContent = "";
  $("#lg-info").textContent = "";
  const email = $("#lg-email").value.trim();
  const pass = $("#lg-pass").value;
  if (!/^\S+@\S+\.\S+$/.test(email)) return (err.textContent = "Enter your email address.");
  if (pass.length < 6) return (err.textContent = "Use a password of at least 6 characters.");
  if (loginMode === "signup" && $("#lg-pass2").value !== pass) return (err.textContent = "The two passwords don't match.");
  writeJSON("jobradar.lastEmail", email);
  await withBusy($("#lg-submit"), loginMode === "signup" ? "Creating…" : "Signing in…", async () => {
    try {
      await JRCloud.remember($("#lg-remember").checked);
      freshSignIn = true;
      if (loginMode === "signup") {
        await JRCloud.signUp(email, pass);
        await JRCloud.sendVerification().catch(() => null); // the app asks you to verify before Gmail and uploads
      }
      else await JRCloud.signIn(email, pass);
      // onUserChanged takes it from here
    } catch (ex) {
      freshSignIn = false;
      err.textContent = authMessage(ex);
    }
  });
}

async function onGoogle() {
  $("#lg-error").textContent = "";
  const started = Date.now();
  try {
    await JRCloud.remember($("#lg-remember").checked);
    freshSignIn = true;
    await JRCloud.google();
  } catch (ex) {
    freshSignIn = false;
    // A window that closes by itself within seconds is Firebase refusing, not you cancelling
    const selfClosed = ex?.code === "auth/popup-closed-by-user" && Date.now() - started < 4000;
    $("#lg-error").textContent = selfClosed
      ? "The Google window closed before you could pick an account. Pop-ups or cookies for this site may be blocked (common in incognito). Try a normal window, or sign in with email and password. (auth/popup-closed-by-user)"
      : authMessage(ex);
    console.warn("Google sign-in:", ex?.code, ex?.message);
  }
}

async function onForgot() {
  const email = $("#lg-email").value.trim();
  $("#lg-error").textContent = "";
  if (!/^\S+@\S+\.\S+$/.test(email)) {
    $("#lg-email").focus();
    return ($("#lg-error").textContent = "Type your email above, then tap Forgot password again.");
  }
  try {
    await JRCloud.resetPassword(email);
    $("#lg-info").textContent = `Reset link sent to ${email}. Check your inbox (and spam).`;
  } catch (ex) {
    $("#lg-error").textContent = authMessage(ex);
  }
}

async function signOut() {
  if (!confirm("Sign out on this device? Your statuses stay safe in your account; this device forgets them until you sign in again.")) return;
  writeJSON(K.mode, undefined);
  writeJSON(K.dirty, undefined);
  store = JRSync.empty();
  saveLocal();
  closeOverlay($("#nav"));
  closeOverlay($("#settings"));
  try {
    await JRCloud.signOut();
  } catch {
    onUserChanged(null);
  }
}

function showApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  animateNext = true;
  // "Use on this device only" with a Job Radar server: the public job list, as before
  if (!JRAccount.enabled() && !data.jobs.length && !session) load({ quiet: true });
  render();
}

function waitForCloud(ms = 8000) {
  if (window.JRCloud) return Promise.resolve(window.JRCloud);
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("sign-in service didn't load")), ms);
    window.addEventListener("jrcloud-loaded", () => {
      clearTimeout(t);
      resolve(window.JRCloud);
    }, { once: true });
  });
}

// ------------------------------------------------------------------ settings
function openSettings() {
  JRAccount.renderSettings();
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
    el("p", { className: "muted", textContent: `${syncLabel()} · signed in as ${session.email || session.name}` }),
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
  $("#alert-score").textContent = JRAccount.enabled() ? JRAccount.profile().threshold ?? 70 : data.alert_score || 70;
  const note = (text) => body.replaceChildren(el("p", { className: "note", textContent: text }));
  if (window.JRNative?.isApp) return note("Phone alerts aren't available inside the Android app yet. Open Job Radar in Chrome on this phone and enable them there.");
  if (!("serviceWorker" in navigator && "PushManager" in window && "Notification" in window)) {
    return note(isIOS() && !isStandalone()
      ? "On iPhone, alerts work only from the installed app: Share → Add to Home Screen, then open Job Radar from the home screen."
      : "This browser doesn't support notifications. Try Chrome on Android, or the installed app on iPhone.");
  }
  const multi = JRAccount.enabled();
  const publicKey = multi ? await JRAccount.pushKey() : data.push?.public_key;
  if (!publicKey && multi) return note("Alerts aren't switched on on this server yet.");
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
        const created = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(publicKey) });
        if (multi) await JRAccount.savePush(created);
        renderAlerts();
      } catch (e) {
        toast(`Couldn't enable alerts: ${e.message}`);
        btn.disabled = false;
      }
    });
    return body.replaceChildren(btn);
  }
  if (multi) {
    const off = el("button", { className: "btn", type: "button", textContent: "Turn off on this device" });
    off.addEventListener("click", async () => {
      await sub.unsubscribe();
      toast("Alerts turned off on this device");
      renderAlerts();
    });
    JRAccount.savePush(sub).catch(() => null); // keep the server's copy current
    return body.replaceChildren(el("p", { className: "note", textContent: "✓ Alerts are on for this device: new matches at or above your threshold." }), el("div", { className: "row" }, off));
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
      await Promise.all([JRAccount.enabled() ? JRAccount.refreshNow() : load(), syncNow()]);
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
  $("#reset-filters").addEventListener("click", clearFilters);
  $("#toggle-filters").addEventListener("click", () => {
    const open = $("#toggle-filters").getAttribute("aria-expanded") !== "true";
    $("#toggle-filters").setAttribute("aria-expanded", String(open));
    $("#filter-panel").hidden = !open;
    JRDesk.setCollapsed("filters", !open);
  });
  $("#refresh").addEventListener("click", () => {
    if (section === "walkins" && JRAccount.enabled()) JRAccount.refreshWalkins();
    JRAccount.enabled() ? JRAccount.refreshNow() : load();
    syncNow();
  });
  $("#open-nav").addEventListener("click", () => openOverlay($("#nav")));
  $("#section-title").addEventListener("click", () => openOverlay($("#nav")));
  $("#sync-dot").addEventListener("click", () => (session ? toast(syncLabel()) : showLogin()));
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
  for (const s of document.querySelectorAll(".stage")) s.addEventListener("click", () => setSection(s.dataset.go));
  $("#src-warn").addEventListener("click", () => toast(`Failed in the last run: ${JRDesk.failedSources()
    .map((f) => `${SOURCE_LABELS[f.source] || f.source} (${f.reason})`).join("; ")}`));
  $("#f-date-field").addEventListener("change", (e) => setFilters({ dateField: e.target.value }));
  $("#f-date-preset").addEventListener("change", (e) => setFilters({ datePreset: e.target.value }));
  $("#f-date-from").addEventListener("change", (e) => setFilters({ dateFrom: e.target.value }));
  $("#f-date-to").addEventListener("change", (e) => setFilters({ dateTo: e.target.value }));
  $("#f-skills-all").addEventListener("click", () => setFilters({ skillMode: "all" }));
  $("#f-skills-any").addEventListener("click", () => setFilters({ skillMode: "any" }));
  window.addEventListener("offline", () => render());
  window.addEventListener("online", () => render());
  for (const b of document.querySelectorAll("[data-export]")) b.addEventListener("click", () => exportCSV(b.dataset.export));
  $("#export").addEventListener("click", exportBackup);
  $("#import-file").addEventListener("change", (e) => {
    if (e.target.files[0]) importBackup(e.target.files[0]);
    e.target.value = "";
  });
  $("#login-form").addEventListener("submit", onLogin);
  $("#lg-google").addEventListener("click", onGoogle);
  $("#lg-forgot").addEventListener("click", onForgot);
  $("#lg-mode").addEventListener("click", () => setLoginMode(loginMode === "signup" ? "signin" : "signup"));
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
  loadFilters();
  const savedSection = readJSON(K.section, "new");
  section = ALL_SECTIONS.some((x) => x.id === savedSection) ? savedSection : "new";
  history.replaceState({ s: section }, "", location.pathname + location.search + location.hash);
  bind();
  applyFiltersToInputs();
  $("#toggle-filters").setAttribute("aria-expanded", String(!JRDesk.isCollapsed("filters", true)));
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

  try {
    appConfig = await (await fetch(`app-config.json?t=${Date.now()}`, { cache: "no-store" })).json();
  } catch {
    appConfig = {};
  }
  // With a Job Radar server, jobs are your own matches and load once you've signed in
  if (!appConfig.api_url) load({ quiet: true });
  if (!cloudConfigured()) {
    if (readJSON(K.mode, null) === "local") showApp();
    else showLogin();
  } else {
    try {
      const cloud = await waitForCloud();
      await cloud.init(firebaseCfg());
      cloud.onUser(onUserChanged); // shows the app or the login page
    } catch (e) {
      // Offline or the sign-in service is blocked: keep working with this device's copy
      const cached = readJSON("jobradar.user", null);
      if (cached || readJSON(K.mode, null) === "local") {
        session = cached;
        showApp();
        setSync("offline");
      } else {
        showLogin();
        $("#lg-error").textContent = `Can't reach the sign-in service (${e.message}). Check your connection.`;
      }
    }
  }
  focusJobFromHash();
}

init();
