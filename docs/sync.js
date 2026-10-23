/* Job Radar status merge rules, shared by the app (cloud.js) and GitHub Actions
   (jobradar/sync.py has the identical rules; tests check they agree).

   State: {statuses:{id:{status,at,appliedAt?,source?}}, saved:{id:job}, sent:{id:iso},
           pending:{id:{at,open}}, gmail:{last_check, processed, events}}
   Per job the newest change wins, the first applied time is never lost, and
   removing a status is a newer "new" record, so undo travels between devices.

   Plain script: the app loads it with <script>, tests load it with require(). */
"use strict";

const JRSync = (() => {
  const KEEP = ["later", "applied", "interview", "offer", "rejected"];
  const empty = () => ({ statuses: {}, saved: {}, sent: {}, pending: {}, gmail: {} });
  function normalize(s) {
    const out = { ...empty(), ...(s || {}) };
    for (const k of ["statuses", "saved", "sent", "pending", "gmail"]) out[k] = { ...(out[k] || {}) };
    return out;
  }
  function merge(a, b) {
    a = normalize(a);
    b = normalize(b);
    const out = empty();
    for (const id of new Set([...Object.keys(a.statuses), ...Object.keys(b.statuses)])) {
      const x = a.statuses[id], y = b.statuses[id];
      const win = !x ? y : !y ? x : String(y.at || "") > String(x.at || "") ? y : x;
      const applied = [x && x.appliedAt, y && y.appliedAt].filter(Boolean).sort()[0];
      out.statuses[id] = applied ? { ...win, appliedAt: applied } : { ...win };
      if (!applied) delete out.statuses[id].appliedAt;
    }
    // pending[id] = {at, open}: you tapped Apply (open) or answered "not yet" (closed)
    for (const id of new Set([...Object.keys(a.pending), ...Object.keys(b.pending)])) {
      const x = a.pending[id], y = b.pending[id];
      out.pending[id] = !x ? y : !y ? x : String(y.at) > String(x.at) ? y : x;
    }
    for (const id of new Set([...Object.keys(a.sent), ...Object.keys(b.sent)])) {
      const v = [a.sent[id], b.sent[id]].filter(Boolean).sort().pop();
      if (v) out.sent[id] = v;
    }
    for (const id of new Set([...Object.keys(a.saved), ...Object.keys(b.saved)])) {
      if (KEEP.includes(out.statuses[id] && out.statuses[id].status)) out.saved[id] = b.saved[id] || a.saved[id];
    }
    // Gmail bookkeeping is written by GitHub Actions; keep the newer copy
    out.gmail = String((b.gmail || {}).last_check || "") >= String((a.gmail || {}).last_check || "") ? b.gmail : a.gmail;
    return out;
  }
  return { merge, normalize, empty };
})();

if (typeof module !== "undefined") module.exports = JRSync;
