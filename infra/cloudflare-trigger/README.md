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

The dashboard cannot fire a deployed cron on demand (its *Schedule* test only works in the editor
preview; against the live URL `/__scheduled` is an ordinary GET), so the worker provides the button.

1. Create the secret once — in the dashboard: *Settings → Variables and Secrets → Add*, type
   **Secret**, name `TRIGGER_SECRET`, any long random value, *Deploy*. Or from a shell:
   `openssl rand -hex 24 | tee /tmp/trigger_secret | npx wrangler secret put TRIGGER_SECRET`.
2. Deploy this code once: `npx wrangler deploy`.
3. Open in a browser (the URL is printed by the deploy; `<SECRET>` is the value from step 1):

   ```
   https://context-window-trigger.<subdomain>.workers.dev/__scheduled?force=1&secret=<SECRET>
   ```

   The page is JSON: `"outcome": "dispatched"` and a `repository_dispatch` run appears in the Actions
   tab within seconds; `"outcome": "error"` carries GitHub's message (a 401/403 means `GH_TOKEN` is
   missing or lacks *Contents: write*). Add `&dry_run=false` for a real episode; without it every
   manual run is a dry run. Drop `force=1` to see what the cron would do right now (`skip` outside
   06:00) without dispatching.

   From a shell, `POST /trigger` with a header does the same and never needs the secret in a URL:

   ```bash
   curl -X POST -H "Authorization: Bearer $TRIGGER_SECRET" \
     "https://context-window-trigger.<subdomain>.workers.dev/trigger"   # add ?dry_run=false for real
   ```

Unauthorized requests only report the decision, never dispatch. Rotate `TRIGGER_SECRET` if a URL
containing it leaks (browser history, screenshots). This proves the deployed worker and the GitHub
token; it does not prove Cloudflare's scheduler fires on Tuesday — for that, check the dashboard's
*Logs* after 06:00 America/New_York or use the next section.

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
