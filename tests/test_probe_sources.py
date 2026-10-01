"""Offline tests for the feed probe's analysis and verdict logic (no network)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from probe_sources import analyze_feed, to_markdown, verdict  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _rss(items: list[tuple[str, datetime, str, str]]) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>https://x/{i}</link><pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>"
        f"<description>{summary}</description>"
        + (f"<content:encoded><![CDATA[{content}]]></content:encoded>" if content else "")
        + "</item>"
        for i, (t, d, summary, content) in enumerate(items)
    )
    return (
        '<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        f"<channel><title>Test</title>{body}</channel></rss>"
    ).encode()


def test_analyze_counts_window_and_text_depth():
    feed = _rss(
        [
            ("fresh", NOW - timedelta(hours=2), "s" * 300, "c" * 3000),
            ("old", NOW - timedelta(days=10), "s" * 50, ""),
        ]
    )
    r = analyze_feed(feed, NOW, window_hours=120)
    assert r["parsed"] and r["feed_type"] == "rss20"
    assert r["entries"] == 2 and r["entries_in_window"] == 1
    assert r["newest_age_hours"] == 2.0
    assert r["entries_with_full_content"] == 1
    assert r["median_summary_chars"] == 175


def test_verdicts():
    assert verdict({"status": 404}) == "DEAD"
    assert verdict({"status": 0}) == "DEAD"
    assert verdict({"status": 403}) == "HTTP 403"
    assert verdict({"status": 200, "parsed": False}) == "NOT_A_FEED"
    assert verdict({"status": 200, "parsed": True, "entries": 0}) == "EMPTY"
    assert verdict({"status": 200, "parsed": True, "entries": 5, "entries_dated": 0}) == "UNDATED"
    assert verdict({"status": 200, "parsed": True, "entries": 5, "entries_dated": 5, "entries_in_window": 0}) == "STALE"
    ok = {"status": 200, "parsed": True, "entries": 4, "entries_dated": 4, "entries_in_window": 2, "entries_with_full_content": 0, "median_summary_chars": 400}
    assert verdict(ok) == "OK"
    assert verdict({**ok, "median_summary_chars": 40}) == "OK_HEADLINES_ONLY"
    assert verdict({**ok, "entries_with_full_content": 3}) == "OK_FULLTEXT"


def test_not_a_feed_html():
    r = analyze_feed(b"<html><body>Not Found</body></html>", NOW, 120)
    assert r["entries"] == 0
    assert verdict({"status": 200, **r}) in ("NOT_A_FEED", "EMPTY")


def test_markdown_lists_dead_feeds_first():
    rows = [
        {"group": "main", "name": "Good", "verdict": "OK", "status": 200, "entries": 3, "entries_in_window": 2, "newest_age_hours": 5, "median_summary_chars": 200, "entries_with_full_content": 0, "elapsed_s": 0.3},
        {"group": "main", "name": "Bad", "verdict": "DEAD", "status": 404, "error": "", "final_url": "https://moved/", "elapsed_s": 0.1},
    ]
    md = to_markdown(rows, 120)
    assert md.index("| main | Bad |") < md.index("| main | Good |")
    assert "DEAD: 1" in md and "OK: 1" in md
    assert "→ https://moved/" in md
