import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from pipeline.checkpoint import Checkpoints
from pipeline.feed import ITUNES_NS, build_feed, rebuild_feeds
from pipeline.stages import cfo
from pipeline.storage import Storage

PODCAST = {
    "title": "Context Window",
    "description": "desc & more",
    "language": "en-us",
    "author": "CW",
    "owner_name": "CW",
    "owner_email": "a@b.c",
    "category": "Technology",
    "explicit": False,
    "image_key": "cover.png",
    "feeds": {"standard": {"key": "feed.xml", "title_suffix": ""}, "extended": {"key": "feed-extended.xml", "title_suffix": " (Extended)"}},
}


def _episode(date, extended=False):
    ep = {
        "date": date,
        "status": "PUBLISHED",
        "title": f"Ep {date}",
        "description": "Today <b>stuff</b> happened",
        "published_at": f"{date}T06:00:00+00:00",
        "deep_dive_picks": [{"title": "Paper", "url": "https://arxiv.org/abs/1"}],
        "newsletter_key": f"episodes/{date}/newsletter.html",
        "audio": {"standard": {"key": f"episodes/{date}/episode_{date}.mp3", "bytes": 12345, "duration_seconds": 1500.4}},
    }
    if extended:
        ep["audio"]["extended"] = {"key": f"episodes/{date}/episode_{date}_aisle.mp3", "bytes": 22222, "duration_seconds": 1900}
    return ep


def test_build_feed_is_valid_rss_with_itunes_tags():
    eps = [_episode("2026-09-24"), _episode("2026-09-25", extended=True), {"date": "2026-09-26", "status": "SAFE_MODE", "audio": {}}]
    xml = build_feed(PODCAST, eps, "standard", lambda k: f"https://cdn.example/{k}", now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel.find("title").text == "Context Window"
    assert channel.find(f"{{{ITUNES_NS}}}owner/{{{ITUNES_NS}}}email").text == "a@b.c"
    assert channel.find(f"{{{ITUNES_NS}}}image").attrib["href"] == "https://cdn.example/cover.png"
    items = channel.findall("item")
    assert len(items) == 2  # safe-mode episode without audio is excluded
    assert items[0].find("title").text == "Ep 2026-09-25"  # newest first
    enc = items[0].find("enclosure")
    assert enc.attrib == {"url": "https://cdn.example/episodes/2026-09-25/episode_2026-09-25.mp3", "length": "12345", "type": "audio/mpeg"}
    assert items[0].find(f"{{{ITUNES_NS}}}duration").text == "00:25:00"
    assert items[0].find("guid").text == "2026-09-25-standard"
    assert "Thu, 24 Sep 2026" in items[1].find("pubDate").text


def test_extended_feed_prefers_aisle_audio_and_falls_back():
    eps = [_episode("2026-09-24"), _episode("2026-09-25", extended=True)]
    xml = build_feed(PODCAST, eps, "extended", lambda k: f"https://cdn.example/{k}")
    root = ET.fromstring(xml)
    urls = [i.find("enclosure").attrib["url"] for i in root.find("channel").findall("item")]
    assert urls == ["https://cdn.example/episodes/2026-09-25/episode_2026-09-25_aisle.mp3", "https://cdn.example/episodes/2026-09-24/episode_2026-09-24.mp3"]
    assert root.find("channel/title").text == "Context Window (Extended)"


def test_local_storage_roundtrip_and_rebuild(settings):
    storage = Storage(settings)
    storage.put_json("episodes/2026-09-25/episode.json", _episode("2026-09-25"))
    storage.put_bytes("episodes/2026-09-25/x.txt", b"hi")
    assert storage.get_json("episodes/2026-09-25/episode.json")["date"] == "2026-09-25"
    assert storage.get_bytes("missing") is None
    assert storage.list_keys("episodes/") == ["episodes/2026-09-25/episode.json", "episodes/2026-09-25/x.txt"]
    urls = rebuild_feeds(settings, storage)
    assert set(urls) == {"standard", "extended"}
    assert storage.exists("feed.xml") and storage.exists("feed-extended.xml")
    assert storage.public_url("feed.xml").startswith("file://")


def test_checkpoints_roundtrip_and_fresh(settings, tmp_path):
    storage = Storage(settings)
    ck = Checkpoints(storage, "2026-09-26", tmp_path / "ep")
    assert ck.load("ingest") is None
    ck.save("ingest", [{"title": "a"}])
    assert ck.load("ingest") == [{"title": "a"}]
    # another runner, same date: only the remote copy exists
    ck2 = Checkpoints(storage, "2026-09-26", tmp_path / "ep2")
    assert ck2.load("ingest") == [{"title": "a"}]
    ck3 = Checkpoints(storage, "2026-09-26", tmp_path / "ep3", fresh=True)
    assert ck3.load("ingest") is None


def test_cfo_cost_and_thresholds(settings):
    cost = cfo.estimate_cost(
        settings,
        [("writer", "claude-opus-5", {"input_tokens": 10_000, "output_tokens": 6_000}), ("editor", "claude-sonnet-5", {"input_tokens": 10_000, "output_tokens": 1_000})],
        tts_chars=50_000,
        serper_queries=100,
    )
    rate = float(settings.load_yaml("budget")["pricing"]["elevenlabs_per_1k_chars"])
    assert cost["llm_by_agent"]["writer"] == round(10_000 / 1e6 * 5 + 6_000 / 1e6 * 25, 4)
    assert cost["tts_usd"] == round(50 * rate, 4)
    assert cost["serper_usd"] == 0.1
    assert cost["total_usd"] > 6.0, "test needs to cross the daily breakdown threshold"
    storage = Storage(settings)
    alerts = cfo.record_episode_cost(settings, storage, "2026-09-26", cost)
    assert any("Daily spend" in a for a in alerts)
    from datetime import date

    import pytest

    # Pin the month: the episode above is dated September and date.today() rolls past it.
    assert cfo.check_budget_before_run(settings, storage, today=date(2026, 9, 26)) == cost["total_usd"]
    storage.put_json(cfo.COSTS_KEY, {"episodes": {"2026-09-01": {"total_usd": 200}}})

    with pytest.raises(cfo.BudgetPaused):
        cfo.check_budget_before_run(settings, storage, today=date(2026, 9, 26))
    settings.ignore_budget = True
    assert cfo.check_budget_before_run(settings, storage, today=date(2026, 9, 26)) == 200
