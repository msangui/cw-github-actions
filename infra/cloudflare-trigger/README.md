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

## Test without waiting for Tuesday

Set `DRY_RUN = "true"` in `wrangler.toml` first so the test run costs only the Claude calls, then:

```bash
echo "GH_TOKEN=github_pat_..." > .dev.vars   # wrangler dev reads secrets from this git-ignored file
npx wrangler dev --test-scheduled
# in another terminal — pick the cron that is 06:00 local right now (10 in summer, 11 in winter):
curl "http://localhost:8787/__scheduled?cron=0+10+*+*+TUE,THU"
```

A run should appear in the repo's Actions tab within seconds with trigger `repository_dispatch`.
Or test the deployed worker: `npx wrangler tail` in one terminal, then trigger from the
dashboard's *Triggers → Cron Triggers → Run now* (button availability varies by plan).

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
