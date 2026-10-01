# Cloudflare cron trigger

Fires the **Daily episode** workflow every Tuesday and Thursday at 06:00 America/New_York
by calling GitHub's `repository_dispatch` API. Free tier is plenty (2 requests a week).

## One-time deploy

1. Create a GitHub token: Settings → Developer settings → Fine-grained tokens → *Generate new token*.
   Repository access: only `msangui/cw-github-actions`. Permission: **Contents: Read and write**
   (Metadata: read is added automatically). `repository_dispatch` needs *Contents* write, not
   *Actions* write — with Actions only, GitHub answers `403 Resource not accessible by personal
   access token` and nothing ever appears in the Actions tab. Set an expiry you'll remember to renew;
   the worker fails silently the day it lapses.

   Check the token before deploying (a `204` is success; this does fire a dry run):

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST \
     -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/msangui/cw-github-actions/dispatches \
     -d '{"event_type":"produce-episode","client_payload":{"dry_run":true}}'
   ```

2. Deploy the worker (needs Node.js):

   ```bash
   cd infra/cloudflare-trigger
   npx wrangler login                 # opens the browser once
   npx wrangler secret put GH_TOKEN   # paste the token when prompted
   npx wrangler deploy
   ```

3. Check it: the deploy prints a `*.workers.dev` URL; opening it returns a JSON health check.
   In the Cloudflare dashboard → Workers → context-window-trigger → *Settings → Triggers*
   you should see both cron expressions.

## Manual trigger (the "run now" button)

The dashboard cannot fire a cron on demand, so the worker exposes one. Set a secret once and deploy:

```bash
openssl rand -hex 24 | npx wrangler secret put TRIGGER_SECRET
npx wrangler deploy
```

Then, with the `*.workers.dev` URL the deploy printed:

```bash
curl -X POST -H "Authorization: Bearer $TRIGGER_SECRET" \
  "https://context-window-trigger.<your-subdomain>.workers.dev/trigger"                 # dry run
curl -X POST -H "Authorization: Bearer $TRIGGER_SECRET" \
  "https://context-window-trigger.<your-subdomain>.workers.dev/trigger?dry_run=false"   # real episode
```

The response is JSON (`ok: true` plus the payload, or the GitHub error text), and a run with trigger
`repository_dispatch` appears in the Actions tab within seconds. This uses the same `dispatch()` as
the cron, so it proves the deployed worker and the token; it does not prove Cloudflare's scheduler
fires — for that, see the next section or simply wait for Tuesday and check the dashboard's *Logs*.

## Test the scheduler without waiting for Tuesday

The worker only dispatches when the New York hour equals `LOCAL_HOUR`, and a manual trigger uses the
current time, so every test below overrides `LOCAL_HOUR` to the current local hour and forces
`DRY_RUN` so nothing is published. Repeated dry runs for the same date are nearly free: the
pipeline resumes from that date's checkpoints.

**End-to-end on Cloudflare (validates the cron scheduler + the token).** There is no "run now"
button in the dashboard and no wrangler command to fire a deployed cron, so deploy a temporary
every-5-minutes schedule, watch it fire, then restore:

```bash
HOUR=$(TZ=America/New_York date +%-H)
npx wrangler deploy --triggers "*/5 * * * *" --var LOCAL_HOUR:$HOUR --var DRY_RUN:true
npx wrangler tail            # within 5 min: "dispatched produce-episode to msangui/cw-github-actions"
npx wrangler deploy          # restore the real schedule and vars from wrangler.toml
```

A run with trigger `repository_dispatch` appears in the repo's Actions tab. The dashboard's *Logs*
tab shows the same lines, and *Settings → Triggers* must read `TUE,THU` again after the last deploy.

**Local (validates the code and the token, not the scheduler):**

```bash
printf 'GH_TOKEN=github_pat_...\nLOCAL_HOUR=%s\nDRY_RUN=true\n' "$(TZ=America/New_York date +%-H)" > .dev.vars
npx wrangler dev --test-scheduled
curl "http://localhost:8787/__scheduled?cron=0+10+*+*+TUE,THU"   # second terminal
```

`.dev.vars` is git-ignored; values in it override `[vars]` for `wrangler dev` only.

## Changing the schedule

Edit `crons` in `wrangler.toml` (UTC) and `LOCAL_HOUR`, then `npx wrangler deploy`.
Keep two crons one hour apart when the target is a US timezone so DST keeps working.
Write weekdays as names (`TUE,THU`): Cloudflare numbers them 1-7 from **Sunday**, unlike standard
cron's 0-6, so numeric weekdays are easy to get wrong. Check the dashboard's "Runs" text after deploying.

## If nothing is being triggered

1. Actions tab → *Daily episode*: are there any runs with event `repository_dispatch`? If every run
   says `workflow_dispatch`, the worker has never reached GitHub.
2. Cloudflare dashboard → Workers → context-window-trigger → *Logs*: a `GitHub dispatch failed:
   HTTP 403` line means the token permission is wrong (see step 1 above); `HTTP 401` means it expired.
   No log lines at all at the expected hour means the cron isn't deployed — run `npx wrangler deploy`.
3. Dashboard → *Settings → Triggers*: both crons listed and spelled `TUE,THU`.
