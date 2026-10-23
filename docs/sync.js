/* Job Radar sync: keeps your statuses identical on every device, for ₹0.

   Your Applied / Interview / Skip choices are stored in sync/state.enc.json in
   your own GitHub repo, encrypted with AES-256-GCM using a key derived from your
   passphrase (PBKDF2-SHA256). The repo is public, so without the passphrase the
   file is unreadable. The app reads and writes it through the GitHub API with a
   fine-grained token you create once per device. GitHub Actions uses the same
   format (jobradar/sync.py) to add statuses it finds in Gmail.

   Plain script: the app loads it with <script>, tests load it with require(). */
"use strict";

const JRSync = (() => {
  const PATH = "sync/state.enc.json";
  const ITERATIONS = 310000;
  const API = "https://api.github.com";
  const subtle = () => (globalThis.crypto && globalThis.crypto.subtle) || null;

  // ---------------------------------------------------------- encoding
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  function b64u(bytes) {
    let s = "";
    const b = new Uint8Array(bytes);
    for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function unb64u(s) {
    const pad = "=".repeat((4 - (s.length % 4)) % 4);
    const bin = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  }
  // GitHub's contents API speaks standard base64 of the file bytes
  const toB64 = (text) => {
    const b = enc.encode(text);
    let s = "";
    for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s);
  };
  const fromB64 = (b64) => dec.decode(Uint8Array.from(atob(b64.replace(/\s/g, "")), (c) => c.charCodeAt(0)));

  // ------------------------------------------------------------ crypto
  async function deriveKey(passphrase, saltB64u, iterations = ITERATIONS) {
    const base = await subtle().importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return subtle().deriveKey(
      { name: "PBKDF2", hash: "SHA-256", salt: unb64u(saltB64u), iterations },
      base, { name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  }
  const exportKey = async (key) => b64u(await subtle().exportKey("raw", key));
  const importKey = (raw) => subtle().importKey("raw", unb64u(raw), { name: "AES-GCM" }, true, ["encrypt", "decrypt"]);
  const newSalt = () => b64u(globalThis.crypto.getRandomValues(new Uint8Array(16)));

  async function seal(state, key, salt, iterations = ITERATIONS) {
    const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
    const ct = await subtle().encrypt({ name: "AES-GCM", iv }, key, enc.encode(JSON.stringify(state)));
    return { v: 1, kdf: "PBKDF2-SHA256", iter: iterations, salt, iv: b64u(iv), ct: b64u(ct), updated: new Date().toISOString() };
  }
  async function open(envelope, key) {
    const pt = await subtle().decrypt({ name: "AES-GCM", iv: unb64u(envelope.iv) }, key, unb64u(envelope.ct));
    return JSON.parse(dec.decode(pt));
  }

  // ------------------------------------------------------------- merge
  // Same rules as jobradar/sync.py: per job the newest change wins; the first
  // applied time is never lost; removing a status is a newer "new" record.
  const KEEP = ["later", "applied", "interview", "offer", "rejected"];
  function empty() {
    return { statuses: {}, saved: {}, sent: {}, pending: {}, gmail: {} };
  }
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

  // -------------------------------------------------------- GitHub API
  function client({ owner, repo, token, branch }) {
    const headers = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    const url = `${API}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
    async function call(path, init = {}) {
      const r = await fetch(url + path, { ...init, headers: { ...headers, ...(init.headers || {}) }, cache: "no-store" });
      if (r.status === 404) return { status: 404 };
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const err = new Error(body.message || `GitHub ${r.status}`);
        err.status = r.status;
        throw err;
      }
      return { status: r.status, body };
    }
    return {
      async repoInfo() {
        const r = await call("");
        if (r.status === 404) {
          const e = new Error("Repository not found, or the token can't see it");
          e.status = 404;
          throw e;
        }
        return r.body;
      },
      async get() {
        const r = await call(`/contents/${PATH}?ref=${encodeURIComponent(branch)}`);
        if (r.status === 404) return null;
        return { sha: r.body.sha, envelope: JSON.parse(fromB64(r.body.content)) };
      },
      async put(envelope, sha, message = "Sync statuses") {
        const r = await call(`/contents/${PATH}`, {
          method: "PUT",
          body: JSON.stringify({ message, content: toB64(JSON.stringify(envelope, null, 1) + "\n"), branch, ...(sha ? { sha } : {}) }),
        });
        return r.body.content.sha;
      },
    };
  }

  return { PATH, ITERATIONS, deriveKey, exportKey, importKey, newSalt, seal, open, merge, normalize, empty, client, b64u, unb64u };
})();

if (typeof module !== "undefined") module.exports = JRSync;
