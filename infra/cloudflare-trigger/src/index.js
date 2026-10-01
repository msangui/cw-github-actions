/**
 * Cloudflare Worker: fires the Context Window GitHub Actions workflow via repository_dispatch.
 *
 * Schedule lives in wrangler.toml. Both 10:00 and 11:00 UTC are scheduled; only the one
 * that is 06:00 in TIMEZONE actually dispatches, so DST is handled without code changes.
 *
 * HTTP surface (all JSON):
 *   GET  /                      health check: current/wanted local hour, dry-run flag, whether manual trigger is on
 *   GET  /__scheduled           run the cron logic now, hour gate included. Dispatches only when authorized.
 *                               `?force=1` skips the hour gate. (wrangler dev intercepts this path itself.)
 *   POST /trigger               dispatch now, no hour gate. Requires authorization.
 * Authorization: `Authorization: Bearer <TRIGGER_SECRET>` header, or `?secret=<TRIGGER_SECRET>` for a browser.
 * Manual requests are dry runs unless `?dry_run=false` is given, so a stray request can't publish.
 * Unauthorized requests get the decision the cron would make but never dispatch.
 */

function localHour(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, hour: "numeric", hour12: false }).formatToParts(date);
  return parseInt(parts.find((p) => p.type === "hour").value, 10) % 24;
}

function gate(env, now) {
  const tz = env.TIMEZONE || "America/New_York";
  const want = parseInt(env.LOCAL_HOUR || "6", 10);
  const hour = localHour(now, tz);
  return { tz, want, hour, pass: hour === want };
}

async function dispatch(env, payload) {
  if (!env.GH_TOKEN) {
    throw new Error("GitHub dispatch failed: GH_TOKEN secret is not set (npx wrangler secret put GH_TOKEN)");
  }
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

function authorized(request, url, env) {
  const secret = env.TRIGGER_SECRET;
  if (!secret) return false;
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${secret}` || url.searchParams.get("secret") === secret;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), { status, headers: { "content-type": "application/json" } });
}

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime);
    const g = gate(env, now);
    if (!g.pass) {
      console.log(`skip: ${now.toISOString()} is ${g.hour}:00 in ${g.tz}, want ${g.want}:00`);
      return;
    }
    const payload = { dry_run: (env.DRY_RUN || "false") === "true" };
    try {
      await dispatch(env, payload);
    } catch (e) {
      console.log(e.message); // visible as a plain log line; rethrow so the cron run is marked failed
      throw e;
    }
    console.log(`dispatched ${env.EVENT_TYPE} to ${env.GITHUB_REPO} at ${now.toISOString()} payload=${JSON.stringify(payload)}`);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const now = new Date();
    const g = gate(env, now);
    const isScheduledTest = request.method === "GET" && url.pathname === "/__scheduled";
    const isTrigger = request.method === "POST" && url.pathname === "/trigger";

    if (isScheduledTest || isTrigger) {
      const force = isTrigger || url.searchParams.get("force") === "1";
      const payload = { dry_run: url.searchParams.get("dry_run") !== "false" };
      const base = { now: now.toISOString(), local_hour_now: g.hour, local_hour_wanted: g.want, timezone: g.tz, payload };

      if (!force && !g.pass) {
        console.log(`manual: skip, ${g.hour}:00 in ${g.tz}, want ${g.want}:00`);
        return json({ ...base, ok: true, dispatched: false, outcome: "skip", hint: "hour gate; add ?force=1 to dispatch anyway" });
      }
      if (!authorized(request, url, env)) {
        const why = env.TRIGGER_SECRET
          ? "unauthorized: send Authorization: Bearer <TRIGGER_SECRET> or ?secret=<TRIGGER_SECRET>"
          : "TRIGGER_SECRET is not set: add it under Settings → Variables and Secrets (type Secret)";
        return json({ ...base, ok: false, dispatched: false, outcome: "would_dispatch", error: why }, 401);
      }
      try {
        await dispatch(env, payload);
      } catch (e) {
        console.log(`manual: ${e.message}`);
        return json({ ...base, ok: false, dispatched: false, outcome: "error", error: e.message }, 502);
      }
      console.log(`manual: dispatched ${env.EVENT_TYPE} to ${env.GITHUB_REPO} payload=${JSON.stringify(payload)}`);
      return json({ ...base, ok: true, dispatched: true, outcome: "dispatched", event_type: env.EVENT_TYPE, repo: env.GITHUB_REPO, actions: `https://github.com/${env.GITHUB_REPO}/actions` });
    }

    return json({
      ok: true,
      repo: env.GITHUB_REPO,
      crons: ["0 10 * * TUE,THU", "0 11 * * TUE,THU"],
      timezone: g.tz,
      local_hour_now: g.hour,
      local_hour_wanted: g.want,
      dry_run: env.DRY_RUN,
      gh_token_set: Boolean(env.GH_TOKEN),
      manual_trigger: env.TRIGGER_SECRET
        ? "GET /__scheduled?force=1&secret=<TRIGGER_SECRET> (browser) or POST /trigger with Authorization: Bearer <TRIGGER_SECRET>"
        : "disabled: set the TRIGGER_SECRET secret",
    });
  },
};
