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

  // Health check only. Manual runs: `gh workflow run daily-episode.yml` or the Actions tab.
  async fetch(request, env) {
    const now = new Date();
    const tz = env.TIMEZONE || "America/New_York";
    return new Response(
      JSON.stringify({ ok: true, repo: env.GITHUB_REPO, crons: ["0 10 * * 3,5", "0 11 * * 3,5"], timezone: tz, local_hour_now: localHour(now, tz), dry_run: env.DRY_RUN }, null, 2),
      { headers: { "content-type": "application/json" } },
    );
  },
};
