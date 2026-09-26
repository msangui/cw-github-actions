"""Publish: upload audio + newsletter + script + episode.json to storage, record headlines
for dedup, rebuild the RSS feeds. Replaces the DB publish activity."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.config import Settings
from pipeline.feed import rebuild_feeds
from pipeline.log import get_logger
from pipeline.memory import HeadlineMemory
from pipeline.stages.aisle_curator import NAMESPACE as AISLE_NS
from pipeline.stages.curator import NAMESPACE as MAIN_NS
from pipeline.stages.stitch import probe_duration
from pipeline.storage import CACHE_LONG, CACHE_SHORT, Storage

log = get_logger(stage="publish")


def ensure_cover(settings: Settings, storage: Storage) -> Optional[str]:
    key = settings.load_yaml("podcast").get("image_key", "cover.png")
    if storage.exists(key):
        return storage.public_url(key)
    if settings.cover_path.exists():
        log.info("Uploading cover art", key=key)
        return storage.put_file(key, settings.cover_path, cache_control=CACHE_SHORT)
    log.warning("No cover art found; Spotify requires one", path=str(settings.cover_path))
    return None


def _audio_entry(storage: Storage, key: str, path: Path) -> dict[str, Any]:
    return {"key": key, "url": storage.public_url(key), "bytes": path.stat().st_size, "duration_seconds": round(probe_duration(path), 2)}


def publish_episode(
    settings: Settings,
    storage: Storage,
    memory: Optional[HeadlineMemory],
    episode_date: str,
    status: str,
    metadata: dict[str, Any],
    script: str,
    stories: list[dict[str, Any]],
    aisle_stories: list[dict[str, Any]],
    audio_path: Optional[str],
    extended_audio_path: Optional[str],
    newsletter_html: Optional[str],
    hallucinations: list[str],
    cost: Optional[dict[str, Any]] = None,
    run_url: Optional[str] = None,
) -> dict[str, Any]:
    base = f"episodes/{episode_date}"
    now = datetime.now(timezone.utc)
    ensure_cover(settings, storage)

    audio: dict[str, Any] = {}
    if audio_path and Path(audio_path).exists():
        key = f"{base}/episode_{episode_date}.mp3"
        storage.put_file(key, audio_path, "audio/mpeg", CACHE_LONG)
        audio["standard"] = _audio_entry(storage, key, Path(audio_path))
    if extended_audio_path and Path(extended_audio_path).exists():
        key = f"{base}/episode_{episode_date}_aisle.mp3"
        storage.put_file(key, extended_audio_path, "audio/mpeg", CACHE_LONG)
        audio["extended"] = _audio_entry(storage, key, Path(extended_audio_path))

    newsletter_key = None
    if newsletter_html:
        newsletter_key = f"{base}/newsletter.html"
        storage.put_bytes(newsletter_key, newsletter_html.encode("utf-8"), "text/html; charset=utf-8", CACHE_SHORT)
    script_key = None
    if script:
        script_key = f"{base}/script.txt"
        storage.put_bytes(script_key, script.encode("utf-8"), "text/plain; charset=utf-8", CACHE_SHORT)

    episode = {
        "date": episode_date,
        "status": status,
        "title": metadata.get("title") or f"Context Window — {episode_date}",
        "description": metadata.get("description", ""),
        "story_count": metadata.get("story_count", len(stories)),
        "word_count": len(script.split()) if script else 0,
        "deep_dive_picks": metadata.get("deep_dive_picks", []),
        "headlines": [{"title": s["title"], "url": s.get("url", ""), "source": s.get("source_name", "")} for s in stories],
        "aisle_headlines": [{"title": s["title"], "url": s.get("url", ""), "source": s.get("source_name", "")} for s in aisle_stories],
        "hallucinations": hallucinations,
        "audio": audio,
        "newsletter_key": newsletter_key,
        "script_key": script_key,
        "published_at": now.isoformat(timespec="seconds"),
        "cost": cost or {},
        "run_url": run_url,
    }
    storage.put_json(f"{base}/episode.json", episode, CACHE_SHORT)

    if memory is not None:
        try:
            added = memory.add(MAIN_NS, episode_date, stories)
            added += memory.add(AISLE_NS, episode_date, aisle_stories)
            memory.save()
            log.info("Headline memory updated", added=added)
        except Exception as e:
            log.warning("Could not update headline memory", error=str(e)[:200])

    feeds = rebuild_feeds(settings, storage) if audio else {}
    result = {
        "episode_json": storage.public_url(f"{base}/episode.json"),
        "audio_url": audio.get("standard", {}).get("url"),
        "extended_audio_url": audio.get("extended", {}).get("url"),
        "newsletter_url": storage.public_url(newsletter_key) if newsletter_key else None,
        "feeds": feeds,
    }
    log.info("Episode published", **{k: v for k, v in result.items() if k != "feeds"}, feeds=list(feeds.values()))
    return result
