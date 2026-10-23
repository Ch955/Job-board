/* Job Radar "Sign in with GitHub" helper, for Cloudflare Workers (free plan).

   GitHub's sign-in ends with a step that needs your OAuth app's client secret,
   which can't live in a public web page. This worker holds it and does that one
   step: it swaps the one-time code GitHub gives the app for an access token.
   It stores nothing and only answers your own app (ALLOWED_ORIGIN).

   Settings → Variables and secrets on the worker:
     GITHUB_CLIENT_ID      (text)    from your GitHub OAuth app
     GITHUB_CLIENT_SECRET  (secret)  from your GitHub OAuth app
     ALLOWED_ORIGIN        (text)    e.g. https://ch955.github.io
*/
export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGIN || "").split(",").map((s) => s.trim()).filter(Boolean);
    const ok = allowed.includes(origin);
    const cors = {
      "Access-Control-Allow-Origin": ok ? origin : "null",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      Vary: "Origin",
    };
    const reply = (status, body) =>
      new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });

    if (request.method === "OPTIONS") return new Response(null, { status: ok ? 204 : 403, headers: cors });
    if (!ok) return reply(403, { error: "origin_not_allowed", origin });
    if (request.method !== "POST") return reply(405, { error: "method_not_allowed" });
    if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET) return reply(500, { error: "worker_not_configured" });

    let input;
    try {
      input = await request.json();
    } catch {
      return reply(400, { error: "bad_json" });
    }
    if (!input || typeof input.code !== "string" || !input.code) return reply(400, { error: "missing_code" });

    const gh = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "User-Agent": "job-radar-login" },
      body: JSON.stringify({
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        code: input.code,
        redirect_uri: typeof input.redirect_uri === "string" ? input.redirect_uri : undefined,
      }),
    });
    const data = await gh.json().catch(() => ({}));
    if (!data.access_token) return reply(400, { error: data.error || "exchange_failed", description: data.error_description || "" });
    return reply(200, { access_token: data.access_token, scope: data.scope || "", token_type: data.token_type || "bearer" });
  },
};
