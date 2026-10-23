/* Job Radar PWA: reads jobs.json (rebuilt every 3h by GitHub Actions), keeps your
   Applied / Interview / Skip choices in localStorage on this device. No server. */
"use strict";

const STORE_KEY = "jobradar.v1";
const FILTER_KEY = "jobradar.filters";
const THEME_KEY = "jobradar.theme";
const SECTION_KEY = "jobradar.section";
// Statuses whose jobs are snapshotted on the device so they survive aging out of jobs.json
const KEEP_STATUSES = ["later", "applied", "interview", "offer", "rejected"];
const DEFAULT_FILTERS = { q: "", minScore: 40, location: "", source: "", remote: "" };

// Each job lives in exactly one section, decided by its status
const SECTIONS = [
  { id: "new", label: "To apply", icon: "🎯", hint: "New matches. Tap Applied once you've submitted and it moves to Applied." },
  { id: "later", label: "Apply later", icon: "⏳", hint: "Parked: company sites, account issues, or apply from the laptop." },
  { id: "applied", label: "Applied", icon: "✅", hint: "Newest first. Move a job on when you hear back." },
  { id: "interview", label: "Interview", icon: "🗓️", hint: "In process: tests and interview rounds." },
  { id: "offer", label: "Offers", icon: "🎉", hint: "" },
  { id: "rejected", label: "Rejected", icon: "✖️", hint: "Kept for your records." },
  { id: "skipped", label: "Skipped", icon: "🙈", hint: "Not for you. Restore one with ↩ To apply." },
];
const GMAIL_SECTIONS = [
  { id: "mail", label: "Recruiter emails", icon: "✉️" },
  { id: "reply", label: "Needs your reply", icon: "💬" },
];
// The buttons a card shows in each section: [target status, label]
const ACTIONS = {
  new: [["applied", "Applied"], ["later", "Later"], ["skipped", "Skip"]],
  later: [["applied", "Applied"], ["skipped", "Skip"], ["new", "↩ To apply"]],
  applied: [["interview", "Interview"], ["rejected", "Rejected"], ["new", "↩ Undo"]],
  interview: [["offer", "Offer"], ["rejected", "Rejected"], ["applied", "↩ Applied"]],
  offer: [["interview", "↩ Interview"]],
  rejected: [["applied", "↩ Applied"]],
  skipped: [["new", "↩ To apply"]],
};
const sectionLabel = (id) => [...SECTIONS, ...GMAIL_SECTIONS].find((x) => x.id === id)?.label || id;

const $ = (sel, root = document) => root.querySelector(sel);

let data = { jobs: [] };
let filters = { ...DEFAULT_FILTERS };
let section = "new";
let reloading = false;
let lastFetch = 0;

// ------------------------------------------------------------------ storage
// Every read/write is guarded: private mode or blocked storage must not break the app.
function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}
function writeJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    toast("Couldn't save on this device (storage blocked). Export a backup.");
  }
}

// store = { statuses: { [id]: { status, at, appliedAt? } }, saved: { [id]: job snapshot } }
function loadStore() {
  const s = readJSON(STORE_KEY, {});
  return { statuses: s.statuses || {}, saved: s.saved || {} };
}
let store = loadStore();
const saveStore = () => writeJSON(STORE_KEY, store);

function statusOf(job) {
  return store.statuses[job.id]?.status || job.status || "new";
}

function setStatus(job, status) {
  const prev = store.statuses[job.id];
  const now = new Date().toISOString();
  if (!status || status === "new") {
    delete store.statuses[job.id];
    delete store.saved[job.id];
  } else {
    store.statuses[job.id] = {
      status,
      at: now,
      // First time it was marked applied; kept when it moves on to interview/offer
      appliedAt: prev?.appliedAt || (status === "applied" || status === "interview" ? now : undefined),
    };
    if (KEEP_STATUSES.includes(status)) {
      // Snapshot so it stays in your list after it ages out of jobs.json
      const { id, title, company, location, url, source, score, matched_keywords, posted_date,
              first_seen, remote, score_breakdown, description_snippet } = job;
      store.saved[job.id] = { id, title, company, location, url, source, score, matched_keywords,
                              posted_date, first_seen, remote, score_breakdown, description_snippet };
    } else {
      delete store.saved[job.id];
    }
  }
  saveStore();
}

// ------------------------------------------------------------------- utils
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
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d}d ago`;
}

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

function scoreClass(score) {
  return score >= 85 ? "high" : score >= 70 ? "mid" : "low";
}

let toastTimer;
function toast(message, action) {
  const el = $("#toast");
  el.replaceChildren(document.createTextNode(message));
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.addEventListener("click", () => {
      el.hidden = true;
      action.run();
    });
    el.append(b);
  }
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), action ? 7000 : 3500);
}

// --------------------------------------------------------------------- data
async function load({ quiet = false } = {}) {
  const btn = $("#refresh");
  btn.classList.add("spinning");
  try {
    const r = await fetch(`jobs.json?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
    data.jobs = Array.isArray(data.jobs) ? data.jobs : [];
    lastFetch = Date.now();
    if (!quiet) toast(`Updated · ${data.jobs.length} jobs`);
  } catch (e) {
    if (!quiet) toast(navigator.onLine ? "Couldn't load jobs.json" : "Offline: showing saved jobs");
  } finally {
    btn.classList.remove("spinning");
  }
  buildFilterOptions();
  render();
  renderAlerts();
}

function allJobs() {
  const ids = new Set(data.jobs.map((j) => j.id));
  const saved = Object.values(store.saved).filter((j) => !ids.has(j.id));
  return data.jobs.concat(saved);
}

function locationLabel(job) {
  return job.score_breakdown?.location_label || (job.remote ? "Remote (India)" : "India");
}

// ------------------------------------------------------------------ filters
function buildFilterOptions() {
  const jobs = allJobs();
  fillSelect($("#f-location"), [...new Set(jobs.map(locationLabel))].sort(), "All of India", filters.location);
  fillSelect($("#f-source"), [...new Set(jobs.map((j) => j.source))].sort(), "All sources", filters.source);
}

function fillSelect(select, values, allLabel, current) {
  const opts = [new Option(allLabel, "")];
  for (const v of values) opts.push(new Option(v, v));
  if (current && !values.includes(current)) opts.push(new Option(current, current));
  select.replaceChildren(...opts);
  select.value = current || "";
}

function applyFiltersToInputs() {
  $("#f-search").value = filters.q;
  $("#f-score").value = filters.minScore;
  $("#f-score-val").textContent = filters.minScore;
  $("#f-location").value = filters.location;
  $("#f-source").value = filters.source;
  $("#f-remote").value = filters.remote;
}

function activeFilterCount() {
  return ["minScore", "location", "source", "remote"].filter((k) => filters[k] !== DEFAULT_FILTERS[k]).length;
}

function matchesFilters(job) {
  const status = statusOf(job);
  // The score floor only hides jobs you haven't acted on
  if ((job.score || 0) < filters.minScore && (status === "new" || status === "skipped")) return false;
  if (filters.location && locationLabel(job) !== filters.location) return false;
  if (filters.source && job.source !== filters.source) return false;
  if (filters.remote === "remote" && !job.remote) return false;
  if (filters.remote === "onsite" && job.remote) return false;
  if (filters.q) {
    const hay = `${job.title} ${job.company} ${job.location} ${(job.matched_keywords || []).join(" ")}`.toLowerCase();
    if (!filters.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

// ------------------------------------------------------------------- render
function sectionJobs(id, jobs = allJobs()) {
  return jobs.filter((j) => statusOf(j) === id && matchesFilters(j));
}

function sortFor(id) {
  const at = (j) => store.statuses[j.id]?.at || "";
  if (id === "applied") return (a, b) => (store.statuses[b.id]?.appliedAt || "").localeCompare(store.statuses[a.id]?.appliedAt || "");
  if (["interview", "offer", "rejected", "later"].includes(id)) return (a, b) => at(b).localeCompare(at(a));
  return (a, b) => (b.score || 0) - (a.score || 0) ||
    String(b.posted_date || b.first_seen || "").localeCompare(String(a.posted_date || a.first_seen || ""));
}

function render() {
  const jobs = allJobs();
  const list = $("#list");
  const isGmail = GMAIL_SECTIONS.some((g) => g.id === section);
  const shown = isGmail ? [] : sectionJobs(section, jobs).sort(sortFor(section));

  $("#section-name").textContent = sectionLabel(section);
  $("#section-count").textContent = isGmail ? "–" : shown.length;
  $("#section-hint").textContent = isGmail ? "" : SECTIONS.find((x) => x.id === section)?.hint || "";
  document.querySelector(".filters").hidden = isGmail;

  if (isGmail) {
    list.replaceChildren(renderGmailPanel(section));
  } else if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    const b = document.createElement("b");
    const anyInSection = jobs.some((j) => statusOf(j) === section);
    b.textContent = anyInSection ? "No jobs match these filters" : section === "new" && !data.jobs.length ? "No jobs yet" : `Nothing in ${sectionLabel(section)} yet`;
    empty.append(b, document.createTextNode(
      anyInSection ? "Try lowering the minimum score or resetting filters."
        : section === "new" ? "The next run (every 3 hours) will fill this in."
        : section === "applied" ? "Tap Applied on a job in To apply and it moves here."
        : ""));
    list.replaceChildren(empty);
  } else {
    list.replaceChildren(...shown.map(renderCard));
  }

  // Stats
  const today = startOfToday(), week = startOfWeek();
  const appliedTimes = Object.values(store.statuses).map((s) => s.appliedAt && new Date(s.appliedAt)).filter(Boolean);
  $("#stat-today").textContent = appliedTimes.filter((d) => d >= today).length;
  $("#stat-week").textContent = appliedTimes.filter((d) => d >= week).length;
  $("#stat-toapply").textContent = sectionJobs("new", jobs).length;
  $("#stat-interview").textContent = jobs.filter((j) => statusOf(j) === "interview").length;

  $("#updated").textContent = data.generated_at
    ? `Updated ${ago(data.generated_at)} · ${data.jobs.length} jobs`
    : "Waiting for the first run";

  const n = activeFilterCount();
  const tf = $("#toggle-filters");
  tf.textContent = n ? `Filters (${n})` : "Filters";
  tf.classList.toggle("has-filters", n > 0);
  renderNav(jobs);
}

function renderNav(jobs = allJobs()) {
  const item = (sec, count) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "nav-item";
    b.setAttribute("aria-current", String(sec.id === section));
    const icon = el("span", { className: "nav-icon", textContent: sec.icon });
    const label = el("span", { className: "nav-label", textContent: sec.label });
    b.append(icon, label);
    if (count !== null) b.append(el("span", { className: "count-pill", textContent: count }));
    b.addEventListener("click", () => {
      setSection(sec.id);
      $("#nav").close();
    });
    return b;
  };
  const counts = {};
  for (const j of jobs) {
    const st = statusOf(j);
    if (st === "new" && !matchesFilters(j)) continue;
    counts[st] = (counts[st] || 0) + 1;
  }
  $("#nav-list").replaceChildren(
    el("p", { className: "nav-group", textContent: "Jobs" }),
    ...SECTIONS.map((sec) => item(sec, counts[sec.id] || 0)),
    el("p", { className: "nav-group", textContent: "Gmail" }),
    ...GMAIL_SECTIONS.map((sec) => item(sec, null)),
  );
}

function setSection(id) {
  section = id;
  writeJSON(SECTION_KEY, id);
  render();
  window.scrollTo({ top: 0 });
}

function renderGmailPanel(id) {
  const panel = el("div", { className: "gmail-panel" });
  const title = id === "mail" ? "Recruiter emails" : "Needs your reply";
  const what = id === "mail"
    ? "Drafted emails to recruiters, with your resume attached, will be listed here."
    : "Interview invites, assessments, recruiter replies and rejections from your Gmail will be listed here, and the matching job moves to Interview or Rejected by itself.";
  panel.append(
    el("h3", { textContent: title }),
    el("p", { textContent: what }),
    el("p", { className: "note", textContent: "Coming with the Gmail step (phase 5). Until then, use Gmail directly:" }),
    el("div", { className: "row" },
      el("a", { className: "btn btn-primary", href: "https://mail.google.com/mail/u/0/#drafts", target: "_blank", rel: "noopener", textContent: "Open Gmail drafts ↗" }),
      el("a", { className: "btn", href: "https://mail.google.com/mail/u/0/#inbox", target: "_blank", rel: "noopener", textContent: "Open inbox ↗" })),
  );
  return panel;
}

function renderCard(job) {
  const card = $("#card-tpl").content.firstElementChild.cloneNode(true);
  const status = statusOf(job);
  card.id = `job-${job.id}`;
  card.dataset.status = status;

  const score = $(".score", card);
  score.textContent = job.score ?? "–";
  score.classList.add(scoreClass(job.score || 0));

  $(".title", card).textContent = job.title;
  $(".company", card).textContent = job.company;

  const meta = $(".meta", card);
  const isNew = status === "new" && new Date(job.first_seen).getTime() >= Date.now() - 86400e3;
  const parts = [job.location, job.remote ? "Remote" : null, job.source, ago(job.posted_date || job.first_seen)];
  if (isNew) meta.append(badge("new", "new"));
  if (job.score_breakdown?.walk_in) meta.append(badge("walk-in", "walkin"));
  for (const p of parts.filter(Boolean)) {
    const s = document.createElement("span");
    s.textContent = p;
    meta.append(s);
  }

  const kw = $(".keywords", card);
  for (const k of job.matched_keywords || []) {
    const li = document.createElement("li");
    li.textContent = k;
    kw.append(li);
  }

  const snippet = $(".snippet", card);
  snippet.textContent = job.description_snippet || "";
  snippet.addEventListener("click", () => snippet.classList.toggle("open"));

  const bd = $(".breakdown", card);
  const b = job.score_breakdown || {};
  const rows = [["Title", b.title], ["Skills", b.skills], ["Location", b.location], ["Experience", b.experience],
                ["Recency", b.recency], ["Penalty", b.penalty]];
  for (const [label, value] of rows) {
    if (value === undefined) continue;
    const s = document.createElement("span");
    s.append(`${label} `);
    const strong = document.createElement("b");
    strong.textContent = value;
    s.append(strong);
    bd.append(s);
  }
  if (b.years_required != null) {
    const s = document.createElement("span");
    s.textContent = `Needs ${b.years_required}+ yrs`;
    bd.append(s);
  }
  score.addEventListener("click", () => (bd.hidden = !bd.hidden));

  const apply = $(".apply", card);
  const url = safeUrl(job.url);
  if (url) {
    apply.href = url;
    apply.addEventListener("click", () => {
      if (statusOf(job) === "new") {
        setTimeout(() => toast("Did you apply?", { label: "Mark applied", run: () => moveJob(job, "applied") }), 600);
      }
    });
  } else {
    apply.removeAttribute("href");
    apply.setAttribute("aria-disabled", "true");
  }

  const rec = store.statuses[job.id];
  const line = $(".status-line", card);
  if (rec?.appliedAt && status !== "new") line.textContent = `Applied ${ago(rec.appliedAt)}` +
    (status !== "applied" && status !== "later" ? ` · ${sectionLabel(status)} ${ago(rec.at)}` : "");
  else if (status === "later" && rec) line.textContent = `Parked ${ago(rec.at)}`;
  else line.remove();

  const actions = $(".card-actions", card);
  for (const [to, label] of ACTIONS[status] || ACTIONS.new) {
    const btn = el("button", { className: "btn", type: "button", textContent: label });
    btn.dataset.to = to;
    btn.addEventListener("click", () => moveJob(job, to));
    actions.append(btn);
  }
  return card;
}

function moveJob(job, to) {
  const before = statusOf(job);
  const beforeRec = store.statuses[job.id] ? { ...store.statuses[job.id] } : null;
  const beforeSaved = store.saved[job.id];
  setStatus(job, to);
  render();
  const msg = to === "new" ? `Back in To apply` : `Moved to ${sectionLabel(to)}`;
  toast(msg, {
    label: "Undo",
    run: () => {
      // Restore exactly, including the original applied time
      if (beforeRec) store.statuses[job.id] = beforeRec; else delete store.statuses[job.id];
      if (beforeSaved) store.saved[job.id] = beforeSaved; else if (!KEEP_STATUSES.includes(before)) delete store.saved[job.id];
      saveStore();
      render();
    },
  });
}

function badge(text, cls) {
  const s = document.createElement("span");
  s.className = `badge ${cls}`;
  s.textContent = text;
  return s;
}

function focusJobFromHash() {
  const m = location.hash.match(/^#job=([\w-]+)/);
  if (!m) return;
  let card = document.getElementById(`job-${m[1]}`);
  const job = allJobs().find((j) => j.id === m[1]);
  if (!card && job) {
    filters = { ...DEFAULT_FILTERS };
    applyFiltersToInputs();
    setSection(statusOf(job));
    card = document.getElementById(`job-${m[1]}`);
  }
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.add("highlight");
    setTimeout(() => card.classList.remove("highlight"), 4000);
  }
  history.replaceState(null, "", location.pathname + location.search);
}

// ------------------------------------------------------------------- backup
function exportBackup() {
  const blob = new Blob([JSON.stringify({ app: "job-radar", version: 1, exported_at: new Date().toISOString(), ...store }, null, 1)],
                        { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `job-radar-backup-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function importBackup(file) {
  try {
    const incoming = JSON.parse(await file.text());
    if (!incoming || typeof incoming.statuses !== "object") throw new Error("not a Job Radar backup");
    let merged = 0;
    for (const [id, s] of Object.entries(incoming.statuses)) {
      const cur = store.statuses[id];
      if (!s?.status) continue;
      if (!cur || String(s.at) > String(cur.at)) {
        store.statuses[id] = s;
        merged++;
      }
    }
    for (const [id, j] of Object.entries(incoming.saved || {})) {
      if (!store.saved[id] && j?.id) store.saved[id] = j;
    }
    saveStore();
    render();
    toast(`Imported ${merged} status${merged === 1 ? "" : "es"}`);
  } catch (e) {
    toast(`Import failed: ${e.message}`);
  }
}

// -------------------------------------------------------------------- theme
const THEMES = ["auto", "light", "dark"];
function applyTheme(theme) {
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = theme;
  $("#theme").setAttribute("aria-label", `Theme: ${theme}`);
  $("#theme").title = `Theme: ${theme}`;
}

// ------------------------------------------------------------------- alerts
function b64uToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function sameKey(a, b) {
  if (!a || !b) return false;
  const x = new Uint8Array(a), y = new Uint8Array(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}

const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function renderAlerts() {
  const body = $("#alerts-body");
  $("#alert-score").textContent = data.alert_score || 70;
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  if (!supported) {
    body.replaceChildren(el("p", { className: "note" },
      isIOS() && !isStandalone()
        ? "On iPhone, alerts work only from the installed app: tap Share → Add to Home Screen, then open Job Radar from your home screen and come back here."
        : "This browser doesn't support notifications. Try Chrome on Android, or the installed app on iPhone."));
    return;
  }
  const publicKey = data.push?.public_key;
  if (!publicKey) {
    body.replaceChildren(el("p", { className: "note" },
      "Alerts aren't switched on yet. Add the VAPID_PRIVATE_KEY secret on GitHub (README: Phone alerts, step 1), run the workflow once, then come back here."));
    return;
  }
  if (Notification.permission === "denied") {
    body.replaceChildren(el("p", { className: "note" },
      "Notifications are blocked for this site. Allow them in your browser's site settings, then reopen this menu."));
    return;
  }

  const reg = await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(r, 4000))]);
  if (!reg) {
    body.replaceChildren(el("p", { className: "note" }, "The app's background worker isn't running. Reload the page and try again."));
    return;
  }
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options?.applicationServerKey, b64uToBytes(publicKey))) {
    await sub.unsubscribe(); // keys were regenerated: old subscription is useless
    sub = null;
  }

  if (!sub) {
    const btn = el("button", { className: "btn btn-primary", type: "button", textContent: "Enable alerts on this device" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const perm = await Notification.requestPermission();
        if (perm !== "granted") throw new Error("permission not granted");
        await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(publicKey) });
        renderAlerts();
      } catch (e) {
        toast(`Couldn't enable alerts: ${e.message}`);
        btn.disabled = false;
      }
    });
    body.replaceChildren(btn);
    return;
  }

  const json = JSON.stringify(sub.toJSON());
  const box = el("textarea", { className: "sub-box", readOnly: true, value: json });
  const copy = el("button", { className: "btn btn-primary", type: "button", textContent: "Copy code" });
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(json);
      toast("Copied");
    } catch {
      box.select();
      toast("Select all and copy manually");
    }
  });
  const off = el("button", { className: "btn", type: "button", textContent: "Turn off on this device" });
  off.addEventListener("click", async () => {
    await sub.unsubscribe();
    toast("Alerts turned off on this device");
    renderAlerts();
  });
  const steps = el("ol", { className: "steps" },
    el("li", {}, "Copy the code below."),
    el("li", {}, "On GitHub, open your repo → Settings → Secrets and variables → Actions."),
    el("li", {}, "Create (or edit) the secret WEBPUSH_SUBSCRIPTIONS and paste the code. For a second phone, put each code on its own line."),
    el("li", {}, "Actions tab → Job Radar → Run workflow → test-notification, to check it works."));
  body.replaceChildren(
    el("p", { className: "note" }, "✓ This device is subscribed. One last step so GitHub knows where to send alerts:"),
    steps, box, el("div", { className: "row" }, copy, off));
}

// --------------------------------------------------------------------- init
function bind() {
  const update = (patch) => {
    filters = { ...filters, ...patch };
    writeJSON(FILTER_KEY, filters);
    render();
  };
  $("#f-search").addEventListener("input", (e) => update({ q: e.target.value.trim() }));
  $("#f-score").addEventListener("input", (e) => {
    $("#f-score-val").textContent = e.target.value;
    update({ minScore: Number(e.target.value) });
  });
  const openNav = () => {
    renderNav();
    $("#nav").showModal();
  };
  $("#open-nav").addEventListener("click", openNav);
  $("#section-title").addEventListener("click", openNav);
  // Tap outside the drawer to close it
  $("#nav").addEventListener("click", (e) => {
    if (e.target === $("#nav")) $("#nav").close();
  });
  $("#f-location").addEventListener("change", (e) => update({ location: e.target.value }));
  $("#f-source").addEventListener("change", (e) => update({ source: e.target.value }));
  $("#f-remote").addEventListener("change", (e) => update({ remote: e.target.value }));
  $("#reset-filters").addEventListener("click", () => {
    filters = { ...DEFAULT_FILTERS };
    writeJSON(FILTER_KEY, filters);
    applyFiltersToInputs();
    render();
  });
  $("#toggle-filters").addEventListener("click", () => {
    const panel = $("#filter-panel");
    panel.hidden = !panel.hidden;
    $("#toggle-filters").setAttribute("aria-expanded", String(!panel.hidden));
  });

  $("#refresh").addEventListener("click", () => load());
  $("#theme").addEventListener("click", () => {
    const cur = readJSON(THEME_KEY, "auto");
    const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
    writeJSON(THEME_KEY, next);
    applyTheme(next);
    toast(`Theme: ${next}`);
  });
  $("#open-menu").addEventListener("click", () => {
    renderAlerts();
    $("#menu").showModal();
  });
  $("#export").addEventListener("click", exportBackup);
  $("#import-file").addEventListener("change", (e) => {
    if (e.target.files[0]) importBackup(e.target.files[0]);
    e.target.value = "";
  });

  // Pick up the latest data when you come back to the app
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - lastFetch > 5 * 60e3) load({ quiet: true });
  });
  // Another tab changed your statuses
  window.addEventListener("storage", (e) => {
    if (e.key === STORE_KEY) {
      store = loadStore();
      render();
    }
  });
  window.addEventListener("hashchange", focusJobFromHash);
}

async function init() {
  applyTheme(readJSON(THEME_KEY, "auto"));
  const savedFilters = readJSON(FILTER_KEY, {});
  delete savedFilters.status; // replaced by sections
  filters = { ...DEFAULT_FILTERS, ...savedFilters };
  const savedSection = readJSON(SECTION_KEY, "new");
  section = [...SECTIONS, ...GMAIL_SECTIONS].some((x) => x.id === savedSection) ? savedSection : "new";
  bind();
  applyFiltersToInputs();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
    // A new version of the app was installed: reload once to use it
    const hadController = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (hadController && !reloading) {
        reloading = true;
        location.reload();
      }
    });
  }
  await load({ quiet: true });
  focusJobFromHash();
}

init();
