# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

The Context Window daily AI podcast pipeline (originally Temporal + FastAPI + Postgres + Next.js at
`msangui/context-window`) rebuilt as one Python process that runs in GitHub Actions and publishes MP3s +
an RSS feed to S3. Read `README.md` first; it documents the run sequence, storage layout and setup.

## Commands

```bash
make install        # pip install -e ".[dev]"  (use a venv)
make test           # pytest (ffmpeg-backed tests skip if ffmpeg is missing)
make lint           # ruff check pipeline tests
make dry-run        # text pipeline with whatever keys are in the env; stubs when none
make run            # full episode → S3 if S3_BUCKET is set, else ./output/site
make rebuild-feed
```

Entry point: `python -m pipeline <run|rebuild-feed|check-config>` (`pipeline/cli.py`).

## Architecture rules

- **No servers, no databases.** State lives in the bucket: `state/*.json`, `work/<date>/`, `episodes/<date>/`.
  `pipeline/storage.py` is the only place that talks to S3; it also has a local-directory backend used by
  tests and by runs with `S3_BUCKET` unset.
- **Every stage is a pure-ish function** `stage(settings, ..., inputs) -> JSON-serializable dict`, wrapped by
  `EpisodeRun.stage()` in `pipeline/workflow.py` which checkpoints the result. Keep stage outputs
  JSON-serializable (no Paths, no datetimes) so checkpoints round-trip.
- **Prompts and tunables are YAML**, not code: `config/agents/*.yaml`, `config/*.yaml`. Don't hardcode a
  prompt, model, voice ID or threshold in Python.
- **Hosts and dynamics live in `config/show.yaml`** and are rendered into the `{{SHOW}}` slot of
  `writer.yaml` / `aisle_writer.yaml` by `pipeline/show.py`. `admin/lib/compile-prompt.ts` is a port of
  it; both must turn the frozen inputs `tests/fixtures/{show,writer,aisle_writer}.yaml` into
  `tests/fixtures/compiled_*_prompt.txt`. Regenerate the goldens only when the compiler changes (the
  command is in `tests/test_show_prompt.py`); config edits from the panel must never break tests.
- **`pipeline/log.py` may report to the admin panel** (`PANEL_URL` + `PANEL_TOKEN`): batched, background
  thread, never raises. Emit run-level facts via `panel.event(...)` from `workflow.py`, not ad hoc HTTP.
- **Claude calls go through `pipeline/llm.py`** (`LLM.call_json`): streaming, structured-output JSON
  schema, retries, per-call logs. Models are the Claude 5 family (`claude-opus-5` default); the 1.x
  `anthropic` SDK has no `temperature` kwarg — legacy models get it via `extra_body`.
- **Degrade, don't crash, on optional services**: no OpenAI key → title-overlap dedup; no Serper →
  coverage 1; no Telegram → silent; no Anthropic key → stub script + auto-approve (used by CI).
  Missing ElevenLabs key or ffmpeg is fatal only after the text stages.
- **Editorial content is load-bearing**: host personas, the verbatim sign-off lines, the
  `# SECTION:READ_THESE` marker (stitch uses it to splice The Aisle), voice settings and the duck curve.
  Change them deliberately, in YAML, and mention it in the commit.

## Admin panel (`admin/`)

Separate Next.js app (Vercel, Clerk). It only talks to this repo through the GitHub API (read config,
commit, `workflow_dispatch`) and receives run events at `POST /api/runs/{id}/events`. Editable files are
allow-listed in `admin/lib/config-files.ts`; edits are comment-preserving YAML patch ops
(`admin/lib/yaml-patch.ts`). Checks: `cd admin && npm run typecheck && npm test && npm run build`.
Contract and setup: `admin/README.md`.

## Status machine

`INGESTING → CURATING → WRITING → EDITING → GENERATING_AUDIO → STITCHING → PUBLISHING → PUBLISHED`
with exits `SAFE_MODE` (editor rejected), `DRY_RUN`, `AUDIO_READY` (`--no-publish`), `PAUSED` (budget),
`FAILED`. `output/episodes/<date>/status.json` records the history.

## Testing

`tests/` covers scoring, dedup/memory, script validation and parsing, marker restoration, feed XML,
storage/checkpoints, CFO math, the show.yaml prompt compiler (bands, placeholder, sign-offs, golden
fixture), the panel reporter (batching, auth header, fail-safety), and (with ffmpeg) real stitching +
publishing using synthetic tone MP3s.
Add a test when changing any of those; live-network behaviour is covered by the CI smoke run.
