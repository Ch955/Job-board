// Runs oauth/worker.js the way Cloudflare does, with GitHub's token endpoint mocked.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("../../oauth/worker.js", import.meta.url), "utf8");
const worker = (await import("data:text/javascript," + encodeURIComponent(src))).default;
const env = { GITHUB_CLIENT_ID: "cid", GITHUB_CLIENT_SECRET: "shh", ALLOWED_ORIGIN: "https://ch955.github.io" };
const seen = [];
globalThis.fetch = async (url, init) => {
  seen.push({ url, body: JSON.parse(init.body) });
  const b = JSON.parse(init.body);
  return new Response(JSON.stringify(b.code === "good" ? { access_token: "gho_x", scope: "public_repo", token_type: "bearer" }
                                                       : { error: "bad_verification_code", error_description: "The code passed is incorrect or expired." }));
};
const call = (method, origin, body) => worker.fetch(new Request("https://w.example/", {
  method, headers: { Origin: origin, "Content-Type": "application/json" }, body: body && JSON.stringify(body) }), env);

let r = await call("OPTIONS", "https://ch955.github.io");
assert.equal(r.status, 204); assert.equal(r.headers.get("Access-Control-Allow-Origin"), "https://ch955.github.io");
r = await call("POST", "https://evil.example", { code: "good" });
assert.equal(r.status, 403); assert.equal(seen.length, 0, "never calls GitHub for other sites");
r = await call("POST", "https://ch955.github.io", { code: "good", redirect_uri: "https://ch955.github.io/Job-board/" });
assert.equal(r.status, 200); assert.deepEqual(await r.json(), { access_token: "gho_x", scope: "public_repo", token_type: "bearer" });
assert.equal(seen[0].url, "https://github.com/login/oauth/access_token");
assert.deepEqual(seen[0].body, { client_id: "cid", client_secret: "shh", code: "good", redirect_uri: "https://ch955.github.io/Job-board/" });
r = await call("POST", "https://ch955.github.io", { code: "bad" });
assert.equal(r.status, 400); assert.equal((await r.json()).error, "bad_verification_code");
r = await call("POST", "https://ch955.github.io", {});
assert.equal(r.status, 400);
r = await worker.fetch(new Request("https://w.example/", { method: "POST", headers: { Origin: "https://ch955.github.io" }, body: "{}" }), { ALLOWED_ORIGIN: "https://ch955.github.io" });
assert.equal((await r.json()).error, "worker_not_configured");
console.log("worker ok");
