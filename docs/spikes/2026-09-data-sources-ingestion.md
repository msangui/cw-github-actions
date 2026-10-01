# Spike: data sources and ingestion for Context Window

**Date:** 2026-09-28 · **Branch:** `claude/data-sources-ingestion-spike-xr7h4s` · **Status:** findings + options, no pipeline behaviour changed

## TL;DR

1. The current setup has three quiet defects that matter more than any new source: a **dead Tier 0 feed**
   (Anthropic News, 404), a **cross-coverage signal that is effectively constant** (Serper always returns 10
   organic hits), and an **"HN signal" the curator prompt scores but nothing collects**. Fixing those is hours,
   not days.
2. The writer produces a 3,400-word script from **300 characters per story**, and the editor fact-checks
   against the same 300 characters. **Full-text enrichment of the ~20 selected stories** is the single biggest
   quality lever available and costs well under $1 per episode.
3. Recommended shape: keep RSS as the backbone, add a `kind` field to `sources.yaml` so ingestion becomes a
   set of small adapters (`rss`, `hn`, `hf_papers`, `github_releases`), add an `enrich` stage between curate
   and write, and replace the Serper count with a real coverage metric. Sequenced in §4; total ≈ 4–6 dev-days
   across three PRs.
4. Almost everything worth adding is **free and keyless**: HN Algolia, Hugging Face Daily Papers, GitHub
   `releases.atom`, community mirrors for the labs that have no feed. Paid search APIs are not needed.
5. Network egress from this session was restricted, so feed liveness is verified only where noted. **Run the
   new `Probe sources` workflow once** (Actions tab) to turn every "unverified" cell below into a number.

## 1. What ingestion does today (read from the code)

Everything comes from `config/sources.yaml`: 50 RSS/Atom feeds for the main show, 5 for The Aisle.
`pipeline/stages/ingest.py` fetches them in a thread pool (httpx, 30 s timeout, fixed User-Agent), parses with
feedparser, keeps the first 50 entries per feed inside a 120 h window, and normalizes each into:

```
{title, url, summary (HTML-stripped, ≤500 chars), published_at, source_name, source_tier, vertical?}
```

Then `coverage.py` asks Serper for each of the top 150 titles, `curator.py` scores (recency + tier + coverage),
dedups (memory → title overlap → embeddings) and asks Claude for the brief. The writer sees each story as
title + URL + **300 chars of summary**; the editor fact-checks the script against the same 300-char blurbs.

### Findings

| # | Finding | Evidence | Impact |
|---|---|---|---|
| F1 | **`Anthropic News` (Tier 0) is dead.** `https://www.anthropic.com/news/rss.xml` returns 404; Anthropic publishes no feed at all (`/rss.xml`, `/feed.xml` also 404). Ingest logs a warning and moves on, so one of the four auto-include lab sources has contributed nothing. | Probed from this session via two independent paths. | **High.** Anthropic announcements only reach the show via secondary outlets. |
| F2 | **The Serper "cross-coverage" signal is a constant.** `coverage.py` posts the title with `num: 10` and returns `len(organic)`. Google returns 10 results for essentially any headline, so nearly every story gets `+3` and every story qualifies as a deep dive (`coverage ≥ 2`). It costs money and discriminates nothing. | `pipeline/stages/coverage.py:19-25`, `curator.py:74-82,195`. | **High.** The only "is anyone else talking about this" signal doesn't work. |
| F3 | **The curator prompt scores an "HN signal" that no code collects.** `config/agents/curator.yaml` says "HN signal: 300+ points=+2, 100+ points=+1"; stories carry no HN data. | Prompt vs. story dict in `ingest.py`. | Medium. The model either ignores the rule or invents the number. |
| F4 | **Writer and editor work from ≤300 characters per story.** No stage fetches an article. "Cross-reference every specific claim against source stories" is against RSS blurbs. | `writer.py:68`, `editor.py:53`. | **High.** Biggest hallucination lever; hard cap on script depth. |
| F5 | **arXiv volume dominates the pool.** 7 arXiv category feeds × 50 entries = up to 350 same-timestamp papers per run, each scoring `recency + tier1 = 4`, identical to a fresh TechCrunch story. Nothing distinguishes a landmark paper from the other 340. | `sources.yaml` lines 21-42; `prescore()`. | Medium. Papers crowd out news on quiet days and the survivors are arbitrary. |
| F6 | **No source health tracking.** A feed that 404s, moves, or becomes a Cloudflare challenge page is one log line in a 150-minute job. | `ingest.py:41-43,79-80`. | Medium. F1 happened and nobody noticed. |
| F7 | **`sources.yaml` has no `kind`.** Every entry is assumed RSS, so a JSON API (HN, HF papers, GitHub releases) means Python changes, against the "tunables are YAML" rule. | `ingest.py:38-64`. | Low today; blocks every option in §3. |
| F8 | **Aisle relevance is keyword matching** on title + blurb against five general retail-trade feeds. AI-in-retail stories are rare there, so most runs pick 2-3 marginal items. | `aisle_curator.py:17-60`. | Medium. Segment quality depends on luck. |
| F9 | **Prompt and config disagree on the window.** The curator prompt says "Exclude: older than 36h"; `sources.yaml` deliberately keeps 120 h so a Tue/Thu cadence misses nothing. | `curator.yaml:11` vs `sources.yaml:4-6`. | Low-medium. On a Tuesday the model is told to drop most of the weekend. |

### Production evidence: the 2026-10-01 run

The first `repository_dispatch` run (Actions run 36867598117, dry run) logged every fetch. The episode came
out "mostly OpenAI blog", and the log says why.

| Group | Feeds | Result |
|---|---|---|
| Non-200 | 16 | Anthropic News 404, Google DeepMind 404, Meta AI 404, Together 404, Replicate 404, Stability 404, LlamaIndex 404, The Register 404, Data Science Weekly 404, a16z 404, Chain Store Age 403, xAI 403, Perplexity 403, The Batch 403, MIT News 403, VentureBeat 429 |
| Alive, zero stories in the window | 9 | Cohere (0 entries), Google Research (legacy host), BAIR, The Gradient, fast.ai, W&B (0 entries), LangChain (0 entries), Chip Huyen, Stanford HAI (0 entries) |
| Live Tier 0 | **1** | OpenAI (1,240 entries in the feed, 10 inside 120 h) |
| arXiv | 7 feeds | **315 of 445** main stories, all scoring the same |
| Everything else | ~20 | TechCrunch 19, TDS 18, Wired 10, Verge 10, MIT TR 10, ZDNet 10, InfoQ 10, KDnuggets 8, Analytics Vidhya 6, IEEE 4, HF 4, MS Research 3, … |

No Serper key was set, so every story had `coverage_count = 1`. Scoring was therefore recency + tier only, OpenAI
was the only outlet with the +3 tier bonus, and its ten posts outranked every tier-1 story of the same age.
The Aisle got one story: Chain Store Age was walled and the remaining four trade feeds carried little AI.
The editor approved the script while flagging five hallucinations, against 300-character blurbs (F4).

### What changed in this branch (the spike's "PR 1")

- `config/sources.yaml` rebuilt from the evidence: six live Tier 0 feeds (OpenAI canonical URL, two
  Anthropic mirrors plus the Claude blog mirror, DeepMind `rss.xml`, Meta scraper), corrected hosts for
  Google Research, The Register, LangChain and Replicate, eleven dead feeds set `active: false` with the
  reason, Techmeme and Hacker News ≥100 points (via hnrss) as community signal, seven GitHub `releases.atom`
  feeds for the agentic-engineering beat, one capped arXiv feed instead of seven, four newsletters, and four
  new Aisle trade feeds. Per-source `max_entries` keeps firehoses in check.
- `ingest.py` records a health row per feed; the run writes `source_health.{json,md}`, the job summary shows
  the unhealthy ones, and a silent Tier 0 feed sends a Telegram warning (F1, F6).
- `curator.py` computes cross-coverage from our own feeds (`cluster_coverage`: distinct outlets with an
  overlapping headline), so coverage varies again without Serper (F2), and caps selection at
  `max_per_source: 3` so one outlet cannot fill the episode.
- `curator.yaml` no longer scores a signal that isn't collected and no longer tells the model to drop
  anything older than 36 h on a five-day window (F3, F9).

Still open from the recommendation: HF Daily Papers and HN points as structured signals (needs `kind`
adapters, PR 2) and full-text enrichment (PR 3).

## 2. Candidate sources

Verification legend: **✅ fetched** in this session · **🔎 search-only** (documented, not fetched from here) ·
**❌ dead**. Cost is per episode unless stated; the show runs ~8 episodes/month.

### 2.1 Lab-direct (Tier 0) — fix before adding anything

| Source | Current URL | Status | Use instead | Depth |
|---|---|---|---|---|
| Anthropic | `anthropic.com/news/rss.xml` | ❌ 404, no official feed exists | Community mirrors, none official: `raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml` (news, 6-hourly) + `…/anthropic_engineering_rss.xml`; `tim-hilde.github.io/anthropic-rss/rss.xml` covers **claude.com/blog** (product posts moved there) with full text. Or self-scrape `anthropic.com/news` (server-rendered) in a `kind: html_list` adapter. | mirror-dependent |
| OpenAI | `openai.com/blog/rss.xml` | 🔎 works; canonical is now `openai.com/news/rss.xml`; HTML pages 403 scripts, feed doesn't; some cloud IPs still get 403 | Switch to `/news/rss.xml`, filter research posts by category | summary |
| Google DeepMind | `deepmind.google/blog/rss/` | 🔎 works as `deepmind.google/blog/rss.xml`; server sometimes gzips regardless of headers (httpx handles it) | keep, fix path | summary |
| Meta AI | `ai.meta.com/blog/rss/` | ❌ fails in every monitor checked; no native feed | community scraper `aabsinthium/meta-ai-blog-rss` (GitHub Pages, 2×/day) or self-scrape | summary |
| Microsoft Research | `microsoft.com/en-us/research/blog/feed/` | ✅ 200, `application/rss+xml`, **full HTML body** in `content:encoded` | keep (best-behaved feed in the set) | full text |
| Google Research | `blog.research.google/feeds/posts/default` | 🔎 legacy Blogspot; current host is `research.google/blog/rss` | switch | summary |
| xAI / Mistral / Cohere / Together / Perplexity / Stability | various `…/rss` | ❌ none has a confirmed native feed (`mistral.ai/rss`, `txt.cohere.com/rss/`, `stability.ai/news-updates?format=rss` all erroring; Together has nothing) | Olshansk/rss-feeds and alan-turing-institute/ai-rss-feeds publish scraped feeds for most (the Turing ones probed 12 days stale, see below); Replicate has official `replicate.com/blog/rss` and `/changelog/rss` (**no `.xml`**). Honest answer for these seven: 1-2 stories a month each, and TechCrunch/The Verge cover the ones that matter. Consider dropping them rather than chasing mirrors. | scraped |
| Hugging Face blog | `huggingface.co/blog/feed.xml` | 🔎 official; high volume incl. community posts | keep, consider tier 1→2 for community posts | summary |

Probed from this session (the few hosts the proxy allowed):

| Feed | Result |
|---|---|
| taobojlen Anthropic **news** mirror | 200, 14 entries, **summaries ~46 chars (headline-only)**, newest item 5 days old → only 0-1 items per 120 h window |
| taobojlen Anthropic **engineering** mirror | 200, 25 entries, headline-only, fresh (newest 4 h) |
| Microsoft Research | 200, 10 entries, **every entry carries full text** (median 546-char summary + full body) |
| Turing Institute scrapes (Mistral, Cohere) | 200 but **newest entry 12 days old** in both: the scraper looks stalled; don't rely on it |
| GitHub `releases.atom` (10 repos) | 403 from this container's egress proxy only; the same URLs fetched fine through another path and carried full release notes |

Effort to fix Tier 0: **~2 h** (YAML edits + one probe run). The mirrors are headline-only and depend on
strangers' cron jobs, which is why Option C (full text) matters for Tier 0 and why the `html_list` adapter in
§3 is worth ~½ day each for Anthropic and Meta.

### 2.2 Community signal (the missing "is this a big deal" input)

| Source | Access | Auth / cost | What you get | Reliability | Fit |
|---|---|---|---|---|---|
| **Hacker News (Algolia)** | `GET hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=points>=100,created_at_i>{now-432000}&hitsPerPage=200` | none; ~10k req/h | every ≥100-pt story of the last 5 days with `points`, `num_comments`, `url`, `title` in **one call** | stable for a decade; 1,000-hit pagination ceiling irrelevant here | **Best.** Match `url`/title against ingested stories → implements F3 exactly, free. Also a source in its own right for things no feed carries (GitHub repos, personal blogs). |
| hnrss.org | `hnrss.org/newest?points=100&count=100` | none | same data as RSS | volunteer service, asks for gentle polling | fallback if Algolia shape changes |
| **Hugging Face Daily Papers** | `GET huggingface.co/api/daily_papers?limit=100` (`date=`, `week=`, `sort=trending`) | none documented (send a free `HF_TOKEN` to be safe); anon 500 req / 5 min | 20-60 curated papers/day with `upvotes`, `numComments`, abstract, AI summary, GitHub links | ✅ used unauthenticated by public Spaces | **Replaces 7 arXiv firehose feeds** with a ranked list (F5). |
| Reddit | RSS `/r/X/.rss` | none, but throttled ~1 req/min/feed and 403/429 from datacenter IPs; `.json` dead since May 2026; OAuth needs manual approval and is arguably "commercial" for a podcast | scores only via OAuth | poor from Actions | skip for now |
| Bluesky | `public.api.bsky.app/xrpc/app.bsky.feed.getFeed?feed=at://…` (curated AI feed generators) | none; `searchPosts` now needs auth | posts + like/repost counts | ok | nice-to-have, weak signal |
| Lobste.rs | `lobste.rs/t/ai.json` | none | `score`, `comment_count` | fine, low volume | marginal |
| Techmeme | `techmeme.com/feed.xml` | none | ~25 human-curated headlines/day with cluster links | strong signal, headline-only | good tier-1 addition for "what mattered" |
| Product Hunt | GraphQL v2, dev token | free, 6,250 pts / 15 min | launches + votes | fine | launch hype ≠ news; skip |
| X / LinkedIn | — | X: $5 per 1k post reads, no search on entry tier; LinkedIn: no API | — | — | skip |

### 2.3 Release and changelog signals (the "agentic engineering" beat)

| Source | Access | Auth / cost | Depth | Gotchas |
|---|---|---|---|---|
| **GitHub `releases.atom`** | `github.com/{owner}/{repo}/releases.atom` | none; web tier, not API quota; honours `If-None-Match` | ✅ **full HTML release notes** in `<content>`, 10 most recent | per-IP throttling of non-browser clients → send ETag, one request per repo per run. Filter `prerelease`/`alpha` and bodies <200 chars (openai/codex shipped 10 alphas in 2 days; llama.cpp 12 builds in one day). |
| GitHub REST `/releases` | `api.github.com/repos/{o}/{r}/releases` | `GITHUB_TOKEN` in Actions = 1,000 req/h, free | JSON with `body`, `prerelease` flag | use only if Atom throttles |
| PyPI project RSS | `pypi.org/rss/project/{name}/releases.xml` | none | ✅ version + link only, **no notes** | join to GitHub for notes |
| npm | `registry.npmjs.org/{pkg}` with `Accept: application/vnd.npm.install-v1+json` | none | versions + dates only | skip unless a JS SDK matters |

Cadence observed 2026-09-28 (fetched): claude-code near-daily with rich notes; anthropic-sdk-python 11 releases
in 5 weeks; openai-python 10 in 9 days; vllm rare but excellent summaries; MCP spec dated stable releases.
Suggested tiering: **spec/major repos** (modelcontextprotocol/*, vllm, transformers, langgraph, pydantic-ai,
google/adk-python, anthropics/claude-code) as tier 1; SDK patch churn as tier 2 with a "minor bump" filter.

### 2.4 Research

| Source | Access | Notes |
|---|---|---|
| arXiv RSS | `rss.arxiv.org/rss/cs.AI` (the `arxiv.org/rss/` URLs in `sources.yaml` are legacy) | ~170-250 items/day in cs.AI alone; Sun-Thu only; ToU 1 req / 3 s. Firehose, no popularity signal. Keep **one** combined feed (`cs.AI+cs.CL`) at tier 2 for the editor to cross-reference, or drop in favour of HF papers. |
| arXiv API | `export.arxiv.org/api/query?id_list=…` | abstracts for chosen IDs; 3 s spacing. Enrichment only. |
| Papers with Code | — | ❌ shut down 2025-07-24, redirects to HF papers. |
| Semantic Scholar | free key, 1 req/s | citation counts lag days; enrichment for "Read These", not discovery. |
| alphaXiv | undocumented `api.alphaxiv.org/papers/v3/feed?sort=Likes&interval=7+Days` | second opinion on trending; may break silently. |

### 2.5 Newsletters

Substack publications expose `/feed` with full text for free posts (Interconnects, Ahead of AI, Latent Space,
One Useful Thing, AI Snake Oil, Last Week in AI, Ben's Bites). TLDR AI has an official `tldr.tech/api/rss/ai`.
Import AI's Substack feed is reported broken; `jack-clark.net/feed/` (already in the list) is the WordPress
mirror. The Batch has no reliable feed. For email-only newsletters, **Kill the Newsletter** turns an inbox into
an Atom feed with zero code; Gmail via IMAP app password also works but adds a personal credential to CI.
Newsletters are weekly digests of stories the feeds already carry, so their value is **commentary for
CLAIRE**, not discovery. Add 3-4 as tier 2, nothing more.

### 2.6 Search and news APIs (Serper and its alternatives)

| API | Cost / 1k | Freshness | Returns body? | Verdict |
|---|---|---|---|---|
| Serper `/search` (current) | $1 → $0.30 at volume | live | no | keep the key, **change the query** (§3 option D) |
| Serper `/news` with `tbs=qdr:w` | same credits | Google News live | no | count **distinct source domains** → real coverage metric |
| Claude `web_search` server tool | **$10** + tokens | live | encrypted snippets, model-reported | 10× Serper; use only for the "missed story" check, `max_uses ≤ 3` |
| Brave News | $5 | live | no | no reason to switch |
| Exa | $7 (+contents) | cached unless `livecrawl` | **yes** | good but expensive for a ranker |
| Tavily | 1,000 free credits/mo, then $8 | `topic: news`, `days` | optional | viable free tier; only if Serper is dropped |
| GDELT DOC 2.0 | free, 1 req / 5 s | 15-min | no | global/low-tier outlets, noisy |
| NewsAPI / GNews free tiers | free | **12-24 h delayed**, non-commercial | no | useless for a news show |
| Bing News API | — | ❌ retired Aug 2025 | — | — |

### 2.7 Full-text enrichment (for F4)

| Approach | Install / cost | Quality | Gotchas |
|---|---|---|---|
| **trafilatura** (client-side, Python) | 2.2.0, Apache-2.0, ~10 MB wheels on top of lxml, no apt deps | F1 0.92 on the maintainer's 990-doc benchmark; best of the open libraries | single-maintainer; pass it HTML fetched with our existing httpx client |
| readability-lxml | tiny | F1 0.83, low recall | second opinion when trafilatura returns <500 chars |
| newspaper4k / goose3 / boilerpy3 | heavier or unmaintained | 0.80-0.81 | not worth it |
| Jina Reader `r.jina.ai/{url}` | keyless, ~20 rpm; free | markdown, handles some JS sites | third-party dependency, no secret needed |
| Firecrawl / Diffbot / Cloudflare Browser Rendering | free tiers cover 320 URLs/month | best against JS and bot walls | another account + secret each |
| **Claude `web_fetch` server tool** | **no per-call charge**, only tokens (~2,500 tokens per 10 kB page → ~20 stories ≈ 50k input tokens ≈ $0.25 on `claude-opus-5`) | Anthropic-hosted, PDF support | only URLs already in the prompt (fine); **no JavaScript rendering**; honours `robots.txt` as `Claude-User`, and most tech media block Anthropic's crawlers; citations conflict with `output_config.format`, so it can't share the existing `call_json` call → two-call pattern |

Bot-blocking reality (search-based, verify with one probe run): TechCrunch, The Verge, Ars, MIT Tech Review
(client-side paywall, full text in HTML) extract fine with a browser-like UA. The Information, Bloomberg,
Reuters, FT, WSJ, NYT will not; degrade to the RSS blurb and flag `full_text: false`. Wayback is no longer a
reliable fallback (NYT and others block `archive.org_bot`).

**Cost of enrichment per episode:** ~20 stories × ~3k tokens fed to writer and editor = ~120k extra input tokens
≈ **$0.60 on Opus 5**, plus $0 for fetching. Against a ~$9 episode this is noise.

### 2.8 The Aisle (CPG / Retail)

Current five: Industry Dive feeds (`grocerydive`, `retaildive`, `fooddive` at `/feeds/news/`) are documented and
low-fluff; `supermarketnews.com/rss.xml` and `chainstoreage.com/rss.xml` need a probe (Supermarket News
re-platformed to Informa and now publishes topic feeds, including a Technology feed). Winsight Grocery Business
merged into Supermarket News in 2024, so do not add it.

Candidates with low PR content and real AI-in-retail coverage: Modern Retail (`/feed/`), RetailWire (`/feed/`),
Digital Commerce 360 (`/feed/`), Just Food (`/feed/`), FoodNavigator-USA topic feed, Progressive Grocer
(`/rss.xml`), McKinsey Insights (`mckinsey.com/insights/rss`) and Bain (`bain.com/rss-feed`) filtered by the
existing keyword list. Company newsrooms (Walmart, Target `corporate.target.com/feeds/news`, Amazon
`aboutamazon.com/rss/feed.rss`, Instacart `tech.instacart.com/feed`, Shopify `shopify.engineering/blog.atom`)
carry the actual AI-deployment announcements but are 100% PR; ingest as a `pr` tier the aisle curator prompt
is told to treat sceptically. PR Newswire / Business Wire industry feeds are 200-300 items/week of fluff; skip.

The bigger Aisle lever is not more feeds but **a Serper `/news` query per run** ("retail AI", "grocery AI",
"CPG demand forecasting AI", `tbs=qdr:w`) merged into the aisle pool. 4 queries × 8 runs = pennies, and it
finds the Walmart/Kroger/Ocado stories wherever they were written up.

## 3. Ingestion architecture options

All options keep the architecture rules: no servers, stage functions returning JSON-serializable dicts,
tunables in YAML, degrade on missing keys.

### Option A — Fix the list, add health reporting (no new code paths)

Edit `sources.yaml` per §2.1, drop 6 of 7 arXiv feeds, add Techmeme and 3-4 newsletters. Make `ingest.py`
return a per-source health summary, print it in the job summary, and send a Telegram warning when a **tier 0**
source yields zero entries. Fix the 36 h/120 h prompt conflict (F9).

- Effort: **½ day**. Risk: none. Value: recovers Anthropic coverage, kills F1/F6/F9.
- Doesn't touch F2/F3/F4.

### Option B — Source adapters keyed by `kind`

```yaml
# sources.yaml v2 (sketch)
main:
  - { name: "OpenAI News",   kind: rss,  url: "https://openai.com/news/rss.xml",                tier: "0" }
  - { name: "Anthropic",     kind: html_list, url: "https://www.anthropic.com/news",
      item_selector: "a[href^='/news/']", tier: "0" }
  - { name: "Hacker News",   kind: hn,   min_points: 100, tier: "1" }
  - { name: "HF Papers",     kind: hf_papers, min_upvotes: 10, tier: "1" }
  - { name: "Claude Code",   kind: github_releases, repo: anthropics/claude-code, tier: "1", skip_prerelease: true }
signals:            # enrich stories that came from elsewhere; don't create stories
  - { kind: hn_points }                 # one Algolia call, match by URL/title
  - { kind: serper_news_coverage, distinct_domains: true }
```

`pipeline/sources/{rss,hn,hf_papers,github_releases,html_list}.py`, each `fetch(source_cfg, window) ->
list[story]` producing the same dict plus optional `signals: {hn_points, upvotes, release_version}`.
`ingest.py` dispatches on `kind` and keeps the thread pool. `prescore()` learns to read `signals`.

- Effort: **1.5-2 days** including tests with recorded fixtures (the repo already has the pattern:
  synthetic inputs, no network in unit tests). `html_list` is the only fragile adapter; ship it last or not at all
  if the mirrors prove stable.
- Value: F3, F5, F7; makes every later addition a YAML change.

### Option C — `enrich` stage (full text for selected stories)

New stage between curate and write: for each of the ≤14 main + ≤5 aisle selected stories, GET the URL with a
browser-like UA and 10 s timeout, extract with trafilatura → readability fallback, keep ≤6,000 chars as
`body`, set `full_text: true/false`, checkpoint to `work/<date>/enrich.json`. Writer and editor prompts get
`body` when present (editor first: it's the fact-checker). Skip domains in a `paywalled:` list in
`config/enrich.yaml`.

Alternative C′: do it inside Claude with `web_fetch`. It removes trafilatura but adds a second LLM call
per story batch, cannot fetch JS-rendered pages, is blocked by outlets that block `Claude-User`, and cannot
share the existing structured-output call. Client-side extraction is the better fit for this codebase.

- Effort: **1-1.5 days** (stage + YAML + prompt edits + tests with saved HTML fixtures). Adds ~$0.60/episode.
- Value: F4, the biggest quality lever. Also gives the newsletter stage real summaries.

### Option D — A coverage signal that measures something

Three sub-options, cheapest first; they compose.

1. **In-run cluster size (free).** `dedup_stories` already computes title overlap; count how many *of our own
   feeds* carried the same story before dropping duplicates and store it as `coverage_count`. 50 feeds is a
   decent proxy for "everyone is writing about this". Effort: 2-3 h.
2. **HN points** as a second axis via the `hn_points` signal in Option B. Effort: included in B.
3. **Serper `/news`, `tbs=qdr:w`, count distinct domains** for the top 40 stories only (instead of 150
   `/search` queries). Same key, fewer credits, an actual number. Effort: 2-3 h.

Update `curator.yaml` so the rubric matches what is computed.

### Option E — Hosted aggregator layer (Inoreader / Feedly)

Outsource fetching and feed health to a reader with an API. Feedly's API is Enterprise-only (~$1.6k/mo);
Inoreader Pro is ~$90/yr with a Google-Reader-style JSON API. It fixes F6 by proxy and nothing else, and
introduces an account, a secret and a vendor between the show and its sources. **Not recommended.**

### Option F — LLM discovery pass ("what did we miss?")

After curation, one `claude-opus-5` call with `web_search_20260209`, `max_uses: 3`, asking for major AI
stories of the last 5 days not in the selected list, returning URLs; anything new is ingested through the
normal scoring. Cost ≈ $0.03 + tokens. Non-deterministic and unattributable to a source tier, so treat
results as tier 2 and never auto-include.

- Effort: **½-1 day** (new server-tool handling in `llm.py`: `server_tool_use` blocks, `pause_turn`,
  encrypted content echo). Value: insurance against feed gaps, not a primary source.

### Option G — Email-borne newsletters

Kill the Newsletter (hosted or self-hosted) gives an Atom URL per newsletter; then it's just Option A YAML.
Effort: 1 h plus subscribing. Only worth it for The Batch and paid newsletters someone already pays for.

### Comparison

| Option | Fixes | Effort | Recurring cost | Risk |
|---|---|---|---|---|
| A. Fix list + health | F1 F6 F9 | ½ d | $0 | none |
| B. `kind` adapters | F3 F5 F7 | 1.5-2 d | $0 | `html_list` fragility |
| C. `enrich` stage | F4 | 1-1.5 d | ~$0.60/ep | bot-blocked outlets → flagged fallback |
| D. Real coverage | F2 | ½ d | ≤ current Serper spend | none |
| E. Hosted aggregator | F6 | ½ d | $90/yr+ | vendor lock-in, no upside |
| F. LLM discovery | gaps | ½-1 d | ~$0.05/ep | non-determinism |
| G. Email newsletters | commentary | 1 h | $0 | third-party service |

## 4. Recommendation and sequencing

**PR 1 (now, ½ day): Option A + D1.** Fix Tier 0 URLs (Anthropic mirror, OpenAI `/news`, DeepMind path,
Meta scraper, Google Research host), drop legacy arXiv URLs to one combined `rss.arxiv.org` feed at tier 2,
add Techmeme and 3 newsletters, add the source-health summary and tier-0 Telegram warning, compute in-run
cluster size as `coverage_count`, reconcile the curator prompt (36 h → recency scoring, remove the HN line
until PR 2). Run the `Probe sources` workflow first and let it decide the Aisle URLs.

**PR 2 (next, ~2 days): Option B + D2/D3.** `kind` adapters for `hn`, `hf_papers`, `github_releases`
(10-12 repos, prerelease filtered), `hn_points` signal, Serper `/news` distinct-domain coverage for the top 40.
Restore the HN line in the curator prompt because it's now real. Replace 6 arXiv feeds with HF Daily Papers.

**PR 3 (~1.5 days): Option C.** `enrich` stage with trafilatura, `config/enrich.yaml` (max chars, paywalled
domains, timeout), `body` into editor then writer, newsletter stage reuses it. Add the ffmpeg-style "skip if
unavailable" degrade: extraction failure → RSS blurb, never a crash.

**Later / optional:** Option F as a monthly experiment behind a flag; Option G if someone wants The Batch;
`html_list` adapter for Anthropic and Meta only if the mirrors go stale.

### What this spike did not verify

- Live status of ~40 candidate feed URLs (egress-blocked here). `docs/spikes/candidate-feeds.yaml` lists
  them; the `Probe sources` workflow reports on all of them in one run.
- Whether OpenAI's feed 403s GitHub's runner IPs (reports say some cloud ranges do). The probe will show it.
- Real per-outlet extraction success rates for Option C; a saved-HTML fixture set should come with PR 3.

### Tooling added by this spike

- `scripts/probe_sources.py` — feed health probe; pure `analyze_feed()` is unit-tested offline.
- `.github/workflows/probe-sources.yml` — manual run, writes the table to the job summary and an artifact.
- `docs/spikes/candidate-feeds.yaml` — every candidate URL from §2, probed alongside the current list.
- `tests/test_probe_sources.py`.
