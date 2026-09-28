# Cloudflare cron trigger

Fires the **Daily episode** workflow every Tuesday and Thursday at 06:00 America/New_York
by calling GitHub's `repository_dispatch` API. Free tier is plenty (2 requests a week).

## One-time deploy

1. Create a GitHub token: Settings → Developer settings → Fine-grained tokens → *Generate new token*.
   Repository access: only `msangui/cw-github-actions`. Permission: **Actions: Read and write**.
   Set an expiry you'll remember to renew.

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
npx wrangler dev --test-scheduled
# in another terminal — pick the cron that is 06:00 local right now (10 in summer, 11 in winter):
curl "http://localhost:8787/__scheduled?cron=0+11+*+*+3,5"
```

A run should appear in the repo's Actions tab within seconds with trigger `repository_dispatch`.
Or test the deployed worker: `npx wrangler tail` in one terminal, then trigger from the
dashboard's *Triggers → Cron Triggers → Run now* (button availability varies by plan).

## Changing the schedule

Edit `crons` in `wrangler.toml` (UTC) and `LOCAL_HOUR`, then `npx wrangler deploy`.
Keep two crons one hour apart when the target is a US timezone so DST keeps working.
Weekday numbers on Cloudflare run 1-7 from **Sunday** (1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu,
6=Fri, 7=Sat), not the 0-6 of standard cron. Check the dashboard's "Runs" text after deploying.
