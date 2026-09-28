# Context Window — GitHub Actions edition

The [Context Window](https://github.com/msangui/context-window) daily AI podcast pipeline, rebuilt as a
single Python job that runs on **GitHub Actions** and publishes to an **S3 bucket** that serves the
MP3s and a Spotify/Apple-compatible **RSS feed**.

Same show, same seven agents, same audio chain — minus Temporal, Postgres, Redis, FastAPI and Next.js.

```
RSS feeds ─▶ Ingest ─▶ Coverage (Serper) ─▶ Curator ─▶ Writer ─▶ Editor ──gate──▶ TTS ─▶ Stitch ─▶ Publish
                                          (+ The Aisle: CPG/Retail curator → writer → editor → TTS)   │
                                                                                                        ▼
                                                            s3://bucket/episodes/<date>/episode_<date>.mp3
                                                            s3://bucket/feed.xml   ◀── submit this to Spotify
```

Each time it is triggered (intended cadence: Tuesday and Thursday, from an external scheduler) the workflow reads ~50 RSS feeds, scores and
dedups stories, has Claude write a FLINT/CLAIRE dialogue script and fact-check it, voices it line by
line with ElevenLabs, mixes the intro jingle, normalizes to -16 LUFS, uploads to S3, regenerates the
feed and pings you on Telegram. Zero servers.

---

## What replaced what

| Original (Temporal stack) | Here |
|---|---|
| Temporal workflow + activities | `pipeline/workflow.py` — plain Python, sequential with a couple of thread pools |
| Temporal retries / replay | Per-stage **checkpoints** in `work/<date>/` (S3). A re-run resumes where it failed. |
| Temporal cron schedule | External trigger: `workflow_dispatch` (Actions tab / `gh`) or `repository_dispatch` webhook. No cron in the repo. |
| `agent_configs` table (prompts, models) | `config/agents/*.yaml` |
| `tool_configs` table (sources, voices, stitch params) | `config/sources.yaml`, `config/voices.yaml`, `config/stitch.yaml` |
| `episodes` / `episode_assets` tables | `episodes/<date>/episode.json` + files in S3 |
| `headlines` + `memory_entries` (pgvector) | `state/memory.json` — 21-day title/URL dedup, optional OpenAI embeddings with cosine distance |
| `llm_logs` table | `output/episodes/<date>/llm_logs/*.json`, uploaded as a run artifact |
| CFO cost tracking (`cost_usd`) | `state/costs.json` + `config/budget.yaml` thresholds; monthly hard-pause still enforced |
| Redis Serper cache | Dropped (one run per day; Serper calls are parallelized instead) |
| FastAPI admin API + Next.js UI | Git. Edit YAML, commit, next run picks it up. Manual triggers via `workflow_dispatch`. |
| Postgres `publish` | RSS feed builder (`pipeline/feed.py`) — `feed.xml` and `feed-extended.xml` |

Everything editorial was carried over verbatim: host personas, script rules, the verbatim sign-off lines,
the scoring rubric, the Aisle relevance keywords, the newsletter template, the ffmpeg duck curve.
Two known defects from the original were fixed along the way: the curator prompt now actually receives
the story list, and the writer prompt includes the `# SECTION:READ_THESE` marker the extended edition
needs to splice The Aisle in.

---

## Setup

### 1. AWS: bucket + IAM role for GitHub (one-time)

```bash
infra/setup-aws.sh <bucket-name> us-east-1 msangui/cw-github-actions
```

This creates the bucket with **public read only** on `feed*.xml`, `cover.png` and `episodes/*`
(`work/` and `state/` stay private), a GitHub OIDC provider, and an IAM role scoped to that bucket.
It prints the `gh variable set` / `gh secret set` commands to run next. No long-lived AWS keys needed.

If you would rather use access keys, skip the repo argument and set `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` as repo secrets instead; the workflow falls back to them when `AWS_ROLE_ARN` is unset.

### 2. GitHub repo variables and secrets

Variables (Settings → Secrets and variables → Actions → **Variables**):

| Variable | Value |
|---|---|
| `S3_BUCKET` | your bucket name |
| `AWS_REGION` | e.g. `us-east-1` |
| `AWS_ROLE_ARN` | printed by `setup-aws.sh` |
| `PUBLIC_BASE_URL` | `https://<bucket>.s3.<region>.amazonaws.com`, or your CloudFront/custom domain |
| `S3_PREFIX` | optional folder inside the bucket |
| `CLAIRE_VOICE_ID` / `FLINT_VOICE_ID` | optional overrides of `config/voices.yaml` |

Secrets:

| Secret | Required |
|---|---|
| `ANTHROPIC_API_KEY` | yes |
| `ELEVENLABS_API_KEY` | yes (without it the run stops after the script — useful for dry runs) |
| `OPENAI_API_KEY` | optional — semantic dedup of headlines |
| `SERPER_API_KEY` | optional — cross-coverage scoring |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional — operator notifications |

### 3. Podcast metadata

Edit `config/podcast.yaml`: title, description, **owner email** (Spotify requires it), category.
Replace `assets/cover.png` with real 1400–3000 px square artwork when you have it
(`make cover` regenerates the placeholder).

### 4. First episode

Actions → **Daily episode** → *Run workflow*. There is no cron; trigger it from your own scheduler with
`gh workflow run daily-episode.yml` or a `repository_dispatch` call (see the header of the workflow file
for the exact `curl`; a fine-grained PAT with *Actions: write* on this repo is enough). Tick **dry_run** the first time to see the script and cost
without spending on TTS; the run artifact contains `brief.json`, `script.txt`, `cost.json` and every LLM
call. Then run it for real. The job summary links the MP3, newsletter and feed URL.

### 5. Submit to Spotify

Once `feed.xml` exists with at least one episode, submit
`https://<bucket>.s3.<region>.amazonaws.com/feed.xml` at
[podcasters.spotify.com](https://podcasters.spotify.com) → *Add your podcast* → *I have a podcast RSS feed*.
Spotify verifies ownership by emailing the `owner_email` in the feed. The same URL works for Apple
Podcasts Connect, Pocket Casts, Overcast, etc.

`feed-extended.xml` is a second feed whose items point at the extended edition (with The Aisle segment)
whenever one was produced, falling back to the standard MP3 otherwise.

---

## Local development

```bash
make install                 # venv recommended: python -m venv .venv && . .venv/bin/activate
cp .env.example .env         # fill in keys; leave S3_BUCKET empty to publish to ./output/site
set -a; . ./.env; set +a

make check-config            # what's configured, what's missing
make dry-run                 # curate → write → edit, no TTS, no publish
make run                     # full episode; with S3_BUCKET empty everything lands in ./output/site
make rebuild-feed            # regenerate feed.xml from episode.json files in storage
make test && make lint
```

With **no API keys at all** the pipeline still runs end to end with a stub script (that is what CI does),
so ingest, scoring, dedup, stitch and feed generation can be exercised for free.

Run flags (`python -m pipeline run --help`):

| flag | effect |
|---|---|
| `--date YYYY-MM-DD` | episode date (default: today UTC) |
| `--dry-run` | text pipeline only |
| `--no-publish` | produce audio, don't upload or touch the feed |
| `--skip-aisle` | no CPG/Retail segment |
| `--fresh` | ignore checkpoints, regenerate everything (also re-pays for TTS lines) |

---

## How a run works

1. **Budget gate** — refuses to start if `state/costs.json` says this month already hit the hard-pause
   threshold (`config/budget.yaml`, default $145). Override with `CW_IGNORE_BUDGET=1`.
2. **Ingest** (main + Aisle feeds in parallel, 120 h window) → **Coverage** (Serper, top 150 stories).
3. **Curate** — deterministic score (recency + tier + coverage) → 3-layer dedup → top 14 → Claude
   writes the editorial brief (order, 60/90/120 s allocations, comedy angles, deep-dive picks, cold open).
4. **Write** — Claude produces the script + metadata as structured JSON; validated for `CLAIRE:`/`FLINT:`
   prefixes and word count, regenerated with feedback on failure.
5. **Edit** — Claude fact-checks against source summaries, fixes inline, returns `approved`.
   Not approved → **SAFE_MODE**: no main audio, headlines posted to Telegram, Aisle audio still produced if
   its own editor pass approved.
6. **TTS** — one ElevenLabs call per line, 300 ms apart. Each line is mirrored to `work/<date>/` so a
   retry only pays for missing lines.
7. **Stitch** — ffmpeg concat → intro duck curve (1.0 → 0.25 over 9–15 s, voice enters at 12 s) →
   `loudnorm I=-16 TP=-1.5 LRA=11`. Extended edition splices Aisle lines before *Read These*.
8. **Publish** — upload MP3(s), `newsletter.html`, `script.txt`, `episode.json`; append headlines to the
   dedup memory; rebuild both feeds; record cost; Telegram summary with the deep-dive links.

Status history is written to `output/episodes/<date>/status.json` and mirrored into the job summary.

### Re-runs and idempotency

The workflow uses a concurrency group, so two triggers for the same day queue rather than race.
A second run of an already **PUBLISHED** date exits immediately unless `--fresh`. A failed run leaves its
checkpoints in `work/<date>/`; the next run for that date resumes from the last completed stage.

### Storage layout

```
s3://<bucket>/[prefix/]
├── feed.xml                      public  ← Spotify
├── feed-extended.xml             public
├── cover.png                     public
├── episodes/<date>/
│   ├── episode.json              public  (metadata, headlines, cost, audio keys)
│   ├── episode_<date>.mp3        public
│   ├── episode_<date>_aisle.mp3  public  (extended edition, when produced)
│   ├── newsletter.html           public
│   └── script.txt                public
├── state/memory.json             private (21-day headline memory for dedup)
├── state/costs.json              private (per-episode spend, monthly totals)
└── work/<date>/                  private (checkpoints + per-line TTS mp3s; safe to delete after publish)
```

---

## Costs

Defaults use `claude-opus-5` for every agent (`config/agents/*.yaml`); switch individual agents to
`claude-sonnet-5` if you want to trade quality for spend. Per episode, roughly: LLM $1–2 (five to six
calls, the writer dominates), ElevenLabs ~$7 at the Creator-plan rate for ~20k characters, Serper
pennies. `cost.json` in every run artifact has the exact numbers and the CFO alerts at $5/day, $120/month
and pauses at $145/month.

## Project structure

```
.github/workflows/daily-episode.yml   cron + manual trigger → produce & publish
.github/workflows/rebuild-feed.yml    regenerate feeds after editing podcast.yaml
.github/workflows/ci.yml              ruff + pytest + keyless smoke run
.github/workflows/probe-sources.yml   manual: feed health report for sources.yaml + docs/spikes/candidate-feeds.yaml
pipeline/
  workflow.py       orchestration, status machine, safe mode, notifications
  cli.py            run / rebuild-feed / check-config
  config.py         env settings + YAML loaders
  llm.py            Claude calls (streaming, structured outputs, retries, logs)
  storage.py        S3 or local directory
  checkpoint.py     stage checkpoints
  memory.py         headline dedup memory
  feed.py           RSS/iTunes feed builder
  stages/           ingest, coverage, curator, aisle_curator, writer, aisle_writer,
                    editor, tts, stitch, newsletter(+template), cfo, publish
config/             podcast, sources, voices, stitch, budget, curation, agents/*.yaml
assets/             intro.mp3 (jingle), cover.png (placeholder artwork)
infra/              setup-aws.sh + IAM/bucket policy templates
scripts/            make_cover.py, probe_sources.py (feed health diagnostic)
docs/spikes/        design spikes (data sources & ingestion, candidate feed list)
tests/              unit tests + ffmpeg-backed audio/publish tests
```
