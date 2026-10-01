"""Ingest with a mocked HTTP transport: story normalization, per-source caps and the health report."""

from datetime import datetime, timedelta, timezone

import httpx

from pipeline.stages import ingest

NOW = datetime.now(timezone.utc)


def _rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>https://x/{i}</link><pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate><description>{s}</description></item>"
        for i, (t, d, s) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'.encode()


def _transport():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "fresh.test":
            return httpx.Response(200, content=_rss([("Fresh story one", NOW - timedelta(hours=2), "<p>hi</p>"), ("Fresh story two", NOW - timedelta(hours=5), "")] + [(f"Bulk {i}", NOW - timedelta(hours=6), "") for i in range(10)]))
        if host == "stale.test":
            return httpx.Response(200, content=_rss([("Old news", NOW - timedelta(days=30), "")]))
        if host == "dead.test":
            return httpx.Response(404)
        if host == "walled.test":
            return httpx.Response(403)
        if host == "html.test":
            return httpx.Response(200, content=b"<html><body>not a feed</body></html>")
        raise httpx.ConnectError("boom")

    return httpx.MockTransport(handler)


SOURCES = [
    {"name": "Fresh", "url": "https://fresh.test/rss", "tier": "0", "max_entries": 4},
    {"name": "Stale", "url": "https://stale.test/rss", "tier": "1"},
    {"name": "Dead", "url": "https://dead.test/rss", "tier": "0"},
    {"name": "Walled", "url": "https://walled.test/rss", "tier": "1"},
    {"name": "Html", "url": "https://html.test/rss", "tier": "2"},
    {"name": "Gone", "url": "https://gone.test/rss", "tier": "1"},
    {"name": "Off", "url": "https://fresh.test/rss", "tier": "1", "active": False},
]


def test_fetch_stories_normalizes_and_caps_per_source():
    stories = ingest.fetch_stories(SOURCES, window_hours=120, max_entries=50, group="t", transport=_transport())
    names = [s["source_name"] for s in stories]
    assert names.count("Fresh") == 4  # per-source max_entries
    assert "Stale" not in names and "Off" not in names
    first = next(s for s in stories if s["title"] == "Fresh story one")
    assert first["summary"] == "hi" and first["source_tier"] == "0" and first["url"] == "https://x/0"
    assert stories[0]["title"] == "Fresh story one"  # newest first


def test_health_report_and_verdicts():
    ingest.fetch_stories(SOURCES, window_hours=120, group="t", transport=_transport())
    rows = {r["source"]: r for r in ingest.health_report() if r["group"] == "t"}
    assert rows["Fresh"]["verdict"] == "OK" and rows["Fresh"]["kept"] == 4
    assert rows["Stale"]["verdict"] == "STALE"
    assert rows["Dead"]["verdict"] == "DEAD" and rows["Dead"]["status"] == 404
    assert rows["Walled"]["verdict"] == "HTTP 403"
    assert rows["Html"]["verdict"] == "EMPTY" and rows["Html"]["error"].startswith("no entries")
    assert rows["Gone"]["verdict"] == "DEAD" and "ConnectError" in rows["Gone"]["error"]
    assert "Off" not in rows

    t0 = ingest.tier0_problems(list(rows.values()))
    assert [r["source"] for r in t0] == ["Dead"]

    md = ingest.health_markdown(list(rows.values()), 120)
    assert "DEAD: 2" in md and "| Dead |" in md and "| Fresh |" not in md  # healthy feeds omitted
    assert ingest.health_markdown([], 120).startswith("_Ingest came from a checkpoint")
