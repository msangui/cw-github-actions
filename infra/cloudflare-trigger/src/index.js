/**
 * Cloudflare Worker: fires the Context Window GitHub Actions workflow via repository_dispatch.
 *
 * Schedule lives in wrangler.toml. Both 10:00 and 11:00 UTC are scheduled; only the one
 * that is 06:00 in TIMEZONE actually dispatches, so DST is handled without code changes.
 */

function localHour(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, hour: "numeric", hour12: false }).formatToParts(date);
  return parseInt(parts.find((p) => p.type === "hour").value, 10) % 24;
}

async function dispatch(env, payload) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`;
  const res = await fetch(url, {
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
  // GitHub returns 204 No Content on success
  if (res.status !== 204) {
    const text = await res.text();
    throw new Error(`GitHub dispatch failed: HTTP ${res.status} ${text.slice(0, 300)}`);
  }
}

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime);
    const tz = env.TIMEZONE || "America/New_York";
    const want = parseInt(env.LOCAL_HOUR || "6", 10);
    const hour = localHour(now, tz);
    if (hour !== want) {
      console.log(`skip: ${now.toISOString()} is ${hour}:00 in ${tz}, want ${want}:00`);
      return;
    }
    const payload = { dry_run: (env.DRY_RUN || "false") === "true" };
    await dispatch(env, payload);
    console.log(`dispatched ${env.EVENT_TYPE} to ${env.GITHUB_REPO} at ${now.toISOString()} payload=${JSON.stringify(payload)}`);
  },

  // GET /         health check
  // POST /trigger  manual dispatch (same code path as the cron, minus the hour gate). Requires the
  //                TRIGGER_SECRET secret: `Authorization: Bearer <secret>`. `?dry_run=false` for a real
  //                episode; anything else is a dry run so a stray request can't publish.
  async fetch(request, env) {
    const url = new URL(request.url);
    const now = new Date();
    const tz = env.TIMEZONE || "America/New_York";

    if (request.method === "POST" && url.pathname === "/trigger") {
      const secret = env.TRIGGER_SECRET;
      const auth = request.headers.get("Authorization") || "";
      if (!secret) return json({ ok: false, error: "TRIGGER_SECRET is not set; run `npx wrangler secret put TRIGGER_SECRET`" }, 503);
      if (auth !== `Bearer ${secret}`) return json({ ok: false, error: "unauthorized" }, 401);
      const payload = { dry_run: url.searchParams.get("dry_run") !== "false" };
      try {
        await dispatch(env, payload);
      } catch (e) {
        console.log(`manual trigger failed: ${e.message}`);
        return json({ ok: false, error: e.message }, 502);
      }
      console.log(`manual trigger: dispatched ${env.EVENT_TYPE} to ${env.GITHUB_REPO} payload=${JSON.stringify(payload)}`);
      return json({ ok: true, dispatched: env.EVENT_TYPE, repo: env.GITHUB_REPO, payload, actions: `https://github.com/${env.GITHUB_REPO}/actions` });
    }

    return json({
      ok: true,
      repo: env.GITHUB_REPO,
      crons: ["0 10 * * TUE,THU", "0 11 * * TUE,THU"],
      timezone: tz,
      local_hour_now: localHour(now, tz),
      local_hour_wanted: parseInt(env.LOCAL_HOUR || "6", 10),
      dry_run: env.DRY_RUN,
      manual_trigger: env.TRIGGER_SECRET ? "POST /trigger with Authorization: Bearer <TRIGGER_SECRET>" : "disabled (no TRIGGER_SECRET)",
    });
  },
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), { status, headers: { "content-type": "application/json" } });
}
