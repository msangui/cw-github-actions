# Context Window — producer panel

A small Next.js app (deployed on Vercel, Clerk sign-in) for running the show without touching YAML by
hand: tune the hosts and their dynamics, edit agent prompts and models, manage sources, voices, mix,
budget and feed metadata, trigger episode runs, watch a run's logs live, and talk to a producer agent that
reads the run and proposes config changes as diffs.

**Git is the only source of truth.** The panel reads `config/**/*.yaml` from the repo's branch head,
keeps your edits as *pending* (browser-local, comment-preserving YAML patches) and, on **Commit**, writes
one git commit through the GitHub API. The next episode run picks it up, exactly like editing YAML by
hand. There is no config database; every change is a diff you can read, revert or blame.

```
Browser ──Clerk──▶ admin (Vercel) ──fine-grained PAT──▶ GitHub  (read config, commit, workflow_dispatch, runs)
                        ▲  bearer PANEL_TOKEN                     │ runs daily-episode.yml
                        └──── POST /api/runs/{id}/events ◀── pipeline/log.py PanelReporter (batched, fail-safe)
                        Upstash Redis (run records + events) · file store in local dev
```

## Tabs

| Tab | Edits | Notes |
|---|---|---|
| Hosts & dials | `config/show.yaml` hosts | bio, traits, quirks, catchphrases, voice notes, seven 0–100 manner dials, plus the five band sentences per dial |
| Dynamics | `config/show.yaml` dynamics | eight show-level dials (joke density, banter, disagreement, interruptions, tangents, callbacks, audience address, pace) + band sentences |
| Agents | `config/agents/*.yaml` | model, effort, max_tokens, system prompt per agent; writer word-count bounds |
| Sources | `config/sources.yaml`, `config/curation.yaml` | feeds (tier / active), ingest window, curation counts |
| Voices & mix | `config/voices.yaml`, `config/stitch.yaml` | ElevenLabs voice settings, duck curve, loudness |
| Budget | `config/budget.yaml` | CFO thresholds and the pricing table cost.json is computed from |
| Podcast | `config/podcast.yaml` | feed metadata (needs a feed rebuild to apply to past episodes) |
| Runs | — | trigger `workflow_dispatch` (dry run by default), list runs, live log/script/cost view |

The **compiled writer prompt** preview (right column on Hosts, Dynamics and Agents) is exactly the system
prompt the writer will receive, recomputed from your pending edits on every change. It is produced by
`lib/compile-prompt.ts`, a line-for-line port of `pipeline/show.py`; both are checked against the same
golden file (`tests/fixtures/compiled_writer_prompt.txt`) so they cannot drift.

The **Producer** drawer (top right) is a Claude chat (`claude-opus-5`, Anthropic SDK, streaming) that sees
the current config *including pending edits*, the compiled writer prompt, and the selected run's logs,
script and cost. It proposes changes through a `propose_config_patch` tool; each proposal is dry-run
against your pending YAML on the server, rendered as a diff card, and only applied when you click
**Apply**. Applying adds it to pending; **Commit** writes it to git. The agent never commits.

## Setup

### 1. Clerk (Google Workspace SSO)

1. Create an application at dashboard.clerk.com. Under *User & authentication → Social connections*
   enable **Google**; under *SSO connections* add your Google Workspace domain so only workspace
   accounts can sign in (Clerk handles the OAuth/SAML dance; no code here cares which).
2. Copy `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`.
3. Set `ALLOWED_EMAIL_DOMAIN=yourcompany.com`. Every API route re-checks the signed-in user's primary
   email against it, so a misconfigured Clerk instance still cannot let a personal Gmail in.

### 2. GitHub token

Create a **fine-grained personal access token** scoped to this one repository with
*Contents: read & write*, *Actions: read & write*, *Metadata: read*. Commits made from the panel are
authored by the token's owner and carry a trailer naming the signed-in panel user.

### 3. Upstash Redis

Vercel Marketplace → Upstash → create a Redis database and link it to the project; it injects
`UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`. Without them the panel writes JSON files under
`admin/.data/` (fine locally, useless on Vercel's read-only filesystem).

### 4. Vercel

Import the repo, set **Root Directory** to `admin`, add the environment variables from `.env.example`.
The agent route declares `maxDuration = 300`; on the Hobby plan Vercel caps functions lower, so use a
Pro project or expect long agent turns to be cut off.

### 5. Wire the pipeline to the panel

In the GitHub repo: variable `PANEL_URL=https://<your-vercel-domain>` and secret `PANEL_TOKEN` equal to
the panel's `PANEL_TOKEN`. `daily-episode.yml` already passes both to the pipeline. When either is
missing the pipeline logs to stdout only, as before.

### Local development

```bash
cd admin && npm install
cp .env.example .env.local     # fill Clerk + GitHub token; leave Upstash empty → ./.data file store
npm run dev                    # http://localhost:3000
npm test && npm run typecheck  # vitest (compiler parity, YAML patches, store folding) + tsc
```

To see live logs from a local pipeline run: `PANEL_URL=http://localhost:3000 PANEL_TOKEN=<token>
make dry-run` in the repo root, then open *Runs → local-…*.

## Events contract (pipeline → panel)

`pipeline/log.py` `PanelReporter` batches everything and POSTs from a background thread:

```
POST {PANEL_URL}/api/runs/{run_id}/events
Authorization: Bearer {PANEL_TOKEN}
Content-Type: application/json

{
  "run": {                       # optional; sent on the first batch and whenever it changes
    "run_id": "17234567890",     # GITHUB_RUN_ID, or "local-YYYYmmdd-HHMMSS"
    "run_url": "https://github.com/…/actions/runs/17234567890",
    "repo": "msangui/cw-github-actions",
    "episode_date": "2026-09-30", "dry_run": false, "skip_aisle": false, "fresh": false,
    "started_at": "2026-09-30T05:00:01+00:00"
  },
  "events": [
    {"seq": 1, "ts": "…", "type": "log", "level": "info", "event": "Budget check passed", "component": "workflow", "month_spent_usd": 12.4},
    {"seq": 2, "ts": "…", "type": "status", "status": "WRITING"},
    {"seq": 3, "ts": "…", "type": "script", "script": "FLINT: …", "title": "…", "word_count": 3412, "hallucinations": [], "changes": 4},
    {"seq": 4, "ts": "…", "type": "cost", "cost": {"llm_usd": 1.62, "llm_by_agent": {"writer": 1.1}, "tts_usd": 6.9, "tts_chars": 21000, "serper_usd": 0.15, "total_usd": 8.67}},
    {"seq": 5, "ts": "…", "type": "result", "status": "PUBLISHED", "audio_url": "…", "cost": {…}}
  ]
}
→ 202 {"ok": true, "received": 5, "event_count": 812, "status": "PUBLISHED"}
```

- `seq` is monotonic per run; the UI dedups on it, so a retried batch is harmless.
- `type: "log"` events are the JSON log lines verbatim (`ts`, `level`, `event`, plus bound context such as
  `stage`, `component`, `date`, and call-specific fields).
- Guarantees on the pipeline side: batches of 50 or every 2 s; one retry per batch; the queue is capped at
  5 000 events; after 5 consecutive failures the reporter disables itself with one warning. Nothing in the
  reporter can raise into the pipeline; `tests/test_panel_reporter.py` pins this down.
- Fallback: `GET /api/runs` and `GET /api/runs/{id}` merge the store with the GitHub Actions API, so a run
  that never reported (panel down, token unset) still shows queued / in progress / failed with a link.

### Panel API (Clerk-protected)

| Route | Purpose |
|---|---|
| `GET /api/config` | all editable YAML files at the branch head + `base_sha` |
| `POST /api/config/commit` | `{base_sha, message, files:{path:text}}` → one commit; 409 if the branch moved |
| `GET /api/runs` / `POST /api/runs` | list runs; dispatch `daily-episode.yml` with `{episode_date, dry_run, fresh, skip_aisle}` |
| `GET /api/runs/{id}?since=<seq>&script=1` | run record + new events (polled every 3 s while live) |
| `POST /api/agent` | producer agent, SSE stream |
| `GET /api/health` | which integrations are configured |

Only paths listed in `lib/config-files.ts` can be read or committed; the agent's tool is held to the same
allow-list, and every committed file must parse as YAML.

## show.yaml schema

```yaml
show:      { name, premise, audience }                # rendered at the top of the writer prompt
hosts:
  FLINT:                                              # key is the speaker tag used in scripts
    name, role, bio, voice_notes: string
    traits, quirks, catchphrases: [string]
    dials: { humor, straightforwardness, warmth, skepticism, verbosity, energy, technical_depth }  # 0–100
  CLAIRE: …
dynamics:  { joke_density, banter, disagreement, interruptions, tangents, callbacks, audience_address, pace }  # 0–100
band_edges: [20, 40, 60, 80]                          # v < 20 → band 0 … v ≥ 80 → band 4
bands:
  host:     { <dial>: [5 sentences, low → high] }
  dynamics: { <dial>: [5 sentences, low → high] }
```

`pipeline/show.py` renders this into the `{{SHOW}}` slot of `config/agents/writer.yaml` and
`aisle_writer.yaml`. The script rules, the verbatim sign-off lines and the `# SECTION:READ_THESE` marker
stay in the agent YAML and are never generated. The model only ever sees band sentences, never numbers:
a dial change matters when it crosses a band edge; editing the band text is the other lever.

## Design notes and tradeoffs

- **Patches, not re-serialization.** Edits are `set/delete/append` ops on the YAML document
  (`lib/yaml-patch.ts`), so comments and key order in the repo files survive a commit. The cost is that
  the UI and the agent share one small op vocabulary instead of arbitrary text edits (the Agents tab's
  prompt textarea is still a single `set`).
- **Optimistic concurrency, no merges.** A commit's parent is the SHA you loaded. If someone pushed in
  between, GitHub refuses the non-fast-forward ref update and the panel asks you to reload and re-apply;
  pending edits are also dropped when the panel notices the head moved. Simpler and safer than
  auto-rebasing YAML.
- **Polling, not websockets.** Vercel functions and Upstash favour short requests; a 3 s poll with
  `since=<seq>` is cheap and survives reconnects. Live logs lag by at most the 2 s batch interval + poll.
- **Agent proposals are dry-run server-side** against the pending files the browser sent, so the card
  you see is the exact diff Apply will produce, and a bad path is rejected before it reaches you.
- **Two keys on purpose.** The panel's `ANTHROPIC_API_KEY` only powers the producer agent; episode
  generation always uses the repo secret, so panel usage never shows up as show cost.
