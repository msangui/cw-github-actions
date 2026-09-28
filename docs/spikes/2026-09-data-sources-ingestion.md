# Spike: data sources and ingestion for Context Window

**Date:** 2026-09-28 · **Branch:** `claude/data-sources-ingestion-spike-xr7h4s` · **Status:** findings + options, no pipeline changes

## TL;DR

<!-- filled in at the end -->

## 1. What ingestion does today (read from the code)

Everything comes from `config/sources.yaml`: 50 RSS/Atom feeds for the main show, 5 for The Aisle. `pipeline/stages/ingest.py`
fetches them in a thread pool (httpx, 30 s timeout, fixed User-Agent), parses with feedparser, keeps the first 50 entries per
feed that fall inside a 120 h window, and normalizes each into:

```
{title, url, summary (HTML-stripped, ≤500 chars), published_at, source_name, source_tier, vertical?}
```

Then `coverage.py` asks Serper for each of the top 150 titles, `curator.py` scores (recency + tier + coverage), dedups
(memory → title overlap → embeddings) and asks Claude for the brief. The writer sees each story as title + URL + **300 chars
of summary**; the editor fact-checks the 3,400-word script against the same 300-char blurbs.

### Findings from reading and probing the current setup

| # | Finding | Evidence | Impact |
|---|---|---|---|
| F1 | **`Anthropic News` (Tier 0) is dead.** `https://www.anthropic.com/news/rss.xml` returns 404; Anthropic has no official feed. Ingest logs a warning and moves on, so one of the four auto-include lab sources has silently contributed nothing. | Probed from this session (404 via two independent paths); community mirrors exist (see §2). | High: the show misses Anthropic announcements unless another outlet picks them up. |
| F2 | **The Serper "cross-coverage" signal is a constant.** `coverage.py` posts the title with `num: 10` and returns `len(organic)`. Google returns 10 organic results for almost any headline, so nearly every story gets `coverage_count ≈ 10` → `+3`, and every story qualifies for deep dives (`coverage ≥ 2`). The signal costs money and discriminates nothing. | `pipeline/stages/coverage.py:19-25`, `curator.py:74-82,195`. | High: the one "is anyone else talking about this" signal doesn't work. |
| F3 | **The curator prompt scores an "HN signal" that no code collects.** `config/agents/curator.yaml` says "HN signal: 300+ points=+2, 100+ points=+1" but stories carry no HN data. | Prompt vs. `ingest.py` story dict. | Medium: the LLM either ignores the rule or hallucinates it. |
| F4 | **Writer and editor work from ≤300 characters per story.** No stage ever fetches the article. The editor's "cross-reference every specific claim against source stories" is against RSS blurbs. | `writer.py:68`, `editor.py:53`. | High: this is the single biggest hallucination lever and the biggest cap on script quality. |
| F5 | **arXiv volume dominates the pool.** 7 arXiv category feeds × 50 entries = up to 350 same-timestamp papers per run, each scoring `recency + tier1 = 4`, the same as a fresh TechCrunch story. Nothing distinguishes a landmark paper from the 340 others. | `sources.yaml` lines 21-42; `prescore()`. | Medium: papers can crowd out news on quiet days, and the ones that make it are arbitrary. |
| F6 | **No source health tracking.** A feed that 404s, moves, or turns into a Cloudflare challenge page is a log line in a 150-minute Actions job. Nobody looks. | `ingest.py:41-43,79-80`. | Medium: F1 happened and nobody noticed. |
| F7 | **`sources.yaml` has no `kind`.** Every entry is assumed to be RSS, so adding a JSON API (HN, arXiv API, GitHub releases) means touching Python, against the "tunables are YAML" rule. | `ingest.py:38-64`. | Low today, blocks every option below. |
| F8 | **The Aisle relevance is keyword matching** on title + blurb, against five general retail-trade feeds. It works, but AI-in-retail stories are rare in those feeds, so most runs pick 2-3 marginal items. | `aisle_curator.py:17-60`. | Medium: segment quality depends on luck. |

A reusable probe now exists for the empirical side of this: `python scripts/probe_sources.py` (or the manual
**Probe sources** workflow) fetches every feed in `sources.yaml` plus `docs/spikes/candidate-feeds.yaml` and reports
DEAD / STALE / HEADLINES_ONLY / OK / OK_FULLTEXT per source. It couldn't run from this session's network (egress is
restricted to a handful of hosts), so **run it once from the Actions tab to turn the table in §2 into hard numbers.**

## 2. Candidate sources

<!-- filled from research below -->

## 3. Ingestion architecture options

<!-- filled below -->

## 4. Recommendation and sequencing

<!-- filled below -->
