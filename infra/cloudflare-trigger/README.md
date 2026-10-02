# Cloudflare cron trigger

Fires the **Daily episode** workflow every Tuesday and Thursday at 10:00 UTC (06:00 New York in
summer, 05:00 in winter) by calling GitHub's `repository_dispatch` API. Every cron firing dispatches;
there is no time-of-day logic in the worker. Free tier is plenty.

## Deploy (once, and after any change here)

```bash
cd infra/cloudflare-trigger
npx wrangler login      # first time on a machine
npx wrangler deploy
```

Then set two secrets in the Cloudflare dashboard: Workers & Pages → `context-window-trigger` →
*Settings → Variables and Secrets → Add*, type **Secret**:

| Name | Value |
|---|---|
| `GH_TOKEN` | A GitHub fine-grained token (Settings → Developer settings → Fine-grained tokens) with repository access to `msangui/cw-github-actions` only and permission **Contents: Read and write**. `repository_dispatch` needs *Contents*, not *Actions*; with Actions only, GitHub answers 403 and nothing ever runs. Note the expiry. |
| `TRIGGER_SECRET` | Any long random string. Enables the manual trigger below. |

(`npx wrangler secret put GH_TOKEN` does the same from a shell.) Secrets survive later deploys.

## Validate it — three steps

1. **Is the token in place?** Open `https://context-window-trigger.<subdomain>.workers.dev/` in a
   browser. It must show `"gh_token_set": true`.
2. **Does the dispatch work?** Open
   `https://context-window-trigger.<subdomain>.workers.dev/__scheduled?secret=<TRIGGER_SECRET>`.
   It runs the exact code the cron runs and answers `"dispatched": true`; within seconds a run with
   trigger `repository_dispatch` appears in
   https://github.com/msangui/cw-github-actions/actions/workflows/daily-episode.yml.
   An error answer carries GitHub's own message (`HTTP 403 Resource not accessible…` = wrong token
   permission). Manual runs are dry runs; add `&dry_run=false` to produce a real episode.
3. **Does Cloudflare's scheduler fire?** Only the scheduler can prove that. Either wait for the next
   Tuesday/Thursday 10:00 UTC and look for the run, or make it fire now: dashboard *Settings →
   Triggers → Cron Triggers*, change the expression to `*/5 * * * *`, save, wait up to five minutes
   for the `repository_dispatch` run (set `DRY_RUN` to `true` under Variables first so the test is
   text-only), then change the cron back to `0 10 * * TUE,THU` and `DRY_RUN` back to `false`.
   The next `npx wrangler deploy` also restores both from `wrangler.toml`.

The dashboard's *Logs* tab shows one line per firing: `dispatched …` or `GitHub dispatch failed: …`.

## Changing the schedule

Edit `crons` in `wrangler.toml` (UTC) and run `npx wrangler deploy`.
