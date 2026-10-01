/**
 * Cloudflare Worker: fires the Context Window GitHub Actions workflow via repository_dispatch.
 *
 * The schedule lives in wrangler.toml. Every cron firing dispatches; there is no time-of-day logic.
 *
 * HTTP surface (all JSON):
 *   GET  /                 health check (also tells you whether the GH_TOKEN secret is set)
 *   GET  /__scheduled      dispatch now — same code as the cron. Requires ?secret=<TRIGGER_SECRET>.
 *   POST /trigger          dispatch now. Requires `Authorization: Bearer <TRIGGER_SECRET>`.
 * Manual requests are dry runs unless `?dry_run=false` is given, so a stray request can't publish.
 */

async function dispatch(env, payload) {
  if (!env.GH_TOKEN) {
    throw new Error("GitHub dispatch failed: GH_TOKEN secret is not set (Settings → Variables and Secrets)");
  }
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "context-window-trigger",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: env.EVENT_TYPE || "produce-episode", client_payload: payload }),
  });
  if (res.status !== 204) {
    // GitHub returns 204 No Content on success
    throw new Error(`GitHub dispatch failed: HTTP ${res.status} ${(await res.text()).slice(0, 300)}`);
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), { status, headers: { "content-type": "application/json" } });
}

export default {
  async scheduled(event, env, ctx) {
    const payload = { dry_run: (env.DRY_RUN || "false") === "true" };
    try {
      await dispatch(env, payload);
    } catch (e) {
      console.log(e.message);
      throw e; // marks the cron run as failed in the dashboard
    }
    console.log(`dispatched ${env.EVENT_TYPE} to ${env.GITHUB_REPO} payload=${JSON.stringify(payload)} cron=${event.cron}`);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const manual = (request.method === "GET" && url.pathname === "/__scheduled") || (request.method === "POST" && url.pathname === "/trigger");

    if (!manual) {
      return json({
        ok: true,
        repo: env.GITHUB_REPO,
        event_type: env.EVENT_TYPE,
        dry_run: env.DRY_RUN,
        gh_token_set: Boolean(env.GH_TOKEN),
        manual_trigger: env.TRIGGER_SECRET ? "GET /__scheduled?secret=<TRIGGER_SECRET>" : "disabled: set the TRIGGER_SECRET secret",
      });
    }

    const secret = env.TRIGGER_SECRET;
    const auth = request.headers.get("Authorization") || "";
    const ok = Boolean(secret) && (auth === `Bearer ${secret}` || url.searchParams.get("secret") === secret);
    if (!ok) {
      const error = secret ? "unauthorized: add ?secret=<TRIGGER_SECRET>" : "TRIGGER_SECRET is not set (Settings → Variables and Secrets, type Secret)";
      return json({ ok: false, dispatched: false, error }, 401);
    }
    const payload = { dry_run: url.searchParams.get("dry_run") !== "false" };
    try {
      await dispatch(env, payload);
    } catch (e) {
      console.log(`manual: ${e.message}`);
      return json({ ok: false, dispatched: false, payload, error: e.message }, 502);
    }
    console.log(`manual: dispatched ${env.EVENT_TYPE} to ${env.GITHUB_REPO} payload=${JSON.stringify(payload)}`);
    return json({ ok: true, dispatched: true, payload, actions: `https://github.com/${env.GITHUB_REPO}/actions/workflows/daily-episode.yml` });
  },
};
