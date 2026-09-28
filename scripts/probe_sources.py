"""Probe every feed in config/sources.yaml (plus any extra URLs) and report how usable each one is.

This is a diagnostic for the data-sources spike, not part of the episode run. For each feed it records:
HTTP status, latency, whether feedparser could parse it, entries in the ingest window, newest entry age,
how much text the entries carry (summary length, full-content availability) and whether dates parse.

Usage:
    python scripts/probe_sources.py                          # all feeds in config/sources.yaml
    python scripts/probe_sources.py --extra https://a/rss    # plus ad-hoc URLs
    python scripts/probe_sources.py --extra-file cands.yaml  # plus a YAML list of {name,url,group}
    python scripts/probe_sources.py --json out.json --md out.md

Exit code is always 0; a dead feed is a finding, not a failure.
"""

from __future__ import annotations

import argparse
import calendar
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import feedparser
import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "ContextWindowBot/1.0 (+podcast pipeline)"  # same UA as pipeline/stages/ingest.py


def _entry_date(entry: Any) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime.fromtimestamp(calendar.timegm(val), tz=timezone.utc)
            except Exception:
                pass
    return None


def _text_len(entry: Any) -> tuple[int, int]:
    """(summary length, full-content length) in characters, HTML tags included."""
    summary = entry.get("summary") or entry.get("description") or ""
    content = ""
    for c in entry.get("content") or []:
        content = max(content, c.get("value") or "", key=len)
    return len(summary), len(content)


def analyze_feed(content: bytes, now: datetime, window_hours: int) -> dict[str, Any]:
    """Pure analysis of a fetched feed body. Safe to unit-test without network."""
    feed = feedparser.parse(content)
    entries = list(feed.entries)
    cutoff = now.timestamp() - window_hours * 3600
    dates = [d for d in (_entry_date(e) for e in entries) if d]
    in_window = [d for d in dates if d.timestamp() >= cutoff]
    summary_lens, content_lens = zip(*(_text_len(e) for e in entries)) if entries else ((), ())
    newest = max(dates) if dates else None
    return {
        "parsed": bool(entries) or not feed.bozo,
        "bozo": bool(feed.bozo),
        "bozo_reason": str(getattr(feed, "bozo_exception", "") or "")[:120] if feed.bozo else "",
        "feed_type": f"{feed.version}" if feed.version else "unknown",
        "feed_title": (feed.feed.get("title") or "")[:80],
        "entries": len(entries),
        "entries_dated": len(dates),
        "entries_in_window": len(in_window),
        "newest_age_hours": round((now - newest).total_seconds() / 3600, 1) if newest else None,
        "median_summary_chars": int(statistics.median(summary_lens)) if summary_lens else 0,
        "entries_with_full_content": sum(1 for n in content_lens if n > 1500),
        "median_content_chars": int(statistics.median(content_lens)) if content_lens else 0,
    }


def probe_one(client: httpx.Client, source: dict[str, Any], now: datetime, window_hours: int) -> dict[str, Any]:
    row: dict[str, Any] = {"group": source.get("group", ""), "name": source.get("name") or source["url"], "url": source["url"], "tier": str(source.get("tier", ""))}
    t0 = time.monotonic()
    try:
        resp = client.get(source["url"])
        row["status"] = resp.status_code
        row["final_url"] = str(resp.url) if str(resp.url) != source["url"] else ""
        row["content_type"] = resp.headers.get("content-type", "")[:60]
        row["bytes"] = len(resp.content)
        row["server"] = resp.headers.get("server", "")[:30]
        if resp.status_code == 200:
            row.update(analyze_feed(resp.content, now, window_hours))
        else:
            row["parsed"] = False
    except Exception as e:  # DNS, TLS, timeout, ...
        row["status"] = 0
        row["error"] = f"{type(e).__name__}: {str(e)[:100]}"
        row["parsed"] = False
    row["elapsed_s"] = round(time.monotonic() - t0, 2)
    row["verdict"] = verdict(row)
    return row


def verdict(row: dict[str, Any]) -> str:
    if row.get("status") != 200:
        return "DEAD" if row.get("status") in (0, 404, 410) else f"HTTP {row.get('status')}"
    if not row.get("parsed"):
        return "NOT_A_FEED"
    if row.get("entries", 0) == 0:
        return "EMPTY"
    if row.get("entries_dated", 0) == 0:
        return "UNDATED"  # ingest keeps undated entries but cannot score recency
    if row.get("entries_in_window", 0) == 0:
        return "STALE"
    if row.get("entries_with_full_content", 0) >= max(1, row.get("entries", 0) // 2):
        return "OK_FULLTEXT"
    if row.get("median_summary_chars", 0) < 120:
        return "OK_HEADLINES_ONLY"
    return "OK"


def load_sources(config_dir: Path) -> list[dict[str, Any]]:
    cfg = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8")) or {}
    out = [{**s, "group": "main"} for s in cfg.get("main", [])]
    out += [{**s, "group": "aisle"} for s in cfg.get("aisle", {}).get("sources", [])]
    return out, int(cfg.get("window_hours", 120))


def to_markdown(rows: list[dict[str, Any]], window_hours: int) -> str:
    cols = ["group", "name", "verdict", "status", "entries", "entries_in_window", "newest_age_hours", "median_summary_chars", "entries_with_full_content", "elapsed_s"]
    heads = ["group", "source", "verdict", "http", "entries", f"in {window_hours}h", "newest (h)", "summary chars", "full-text entries", "s"]
    lines = [f"| {' | '.join(heads)} |", f"|{'---|' * len(heads)}"]
    for r in sorted(rows, key=lambda r: (r["group"], r["verdict"].startswith("OK"), r["name"])):
        lines.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c, "")) for c in cols) + " |")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    notes = [f"- {r['name']}: {r.get('error') or r.get('bozo_reason') or ''} {('→ ' + r['final_url']) if r.get('final_url') else ''}".rstrip() for r in rows if r.get("error") or r.get("bozo_reason") or r.get("final_url")]
    return "\n".join([f"**{len(rows)} feeds** — {summary}", "", *lines, "", "Notes:", *notes]) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", default=str(REPO_ROOT / "config"))
    ap.add_argument("--extra", action="append", default=[], help="extra feed URL (repeatable)")
    ap.add_argument("--extra-file", help="YAML list of {name, url, group?} to probe as well")
    ap.add_argument("--only-extra", action="store_true", help="skip config/sources.yaml")
    ap.add_argument("--window-hours", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--json", help="write full results here")
    ap.add_argument("--md", help="write a markdown table here")
    args = ap.parse_args(argv)

    sources, cfg_window = load_sources(Path(args.config_dir))
    if args.only_extra:
        sources = []
    for url in args.extra:
        sources.append({"name": url, "url": url, "group": "extra"})
    if args.extra_file:
        for s in yaml.safe_load(Path(args.extra_file).read_text(encoding="utf-8")) or []:
            sources.append({"group": "candidate", **s})
    window = args.window_hours or cfg_window
    now = datetime.now(timezone.utc)

    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=args.timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [pool.submit(probe_one, client, s, now, window) for s in sources]
            for fut in as_completed(futures):
                rows.append(fut.result())

    md = to_markdown(rows, window)
    print(md)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({"probed_at": now.isoformat(timespec="seconds"), "window_hours": window, "feeds": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
