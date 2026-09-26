"""RSS 2.0 + iTunes feed builder. The output is what you submit to Spotify for Podcasters
/ Apple Podcasts. Episodes are read from <storage>/episodes/<date>/episode.json."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Callable
from xml.sax.saxutils import escape

from pipeline.config import Settings
from pipeline.log import get_logger
from pipeline.storage import CACHE_SHORT, Storage

log = get_logger(component="feed")

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"


def _rfc2822(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def _hms(seconds: float) -> str:
    s = int(round(seconds or 0))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _cdata(text: str) -> str:
    return "<![CDATA[" + (text or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _pick_audio(ep: dict[str, Any], variant: str) -> dict[str, Any] | None:
    audio = ep.get("audio") or {}
    if variant == "extended" and audio.get("extended"):
        return audio["extended"]
    return audio.get("standard") or (audio.get("extended") if variant == "extended" else None)


def build_feed(podcast: dict[str, Any], episodes: list[dict[str, Any]], variant: str, public_url: Callable[[str], str], now: datetime | None = None) -> str:
    feeds_cfg = podcast.get("feeds", {})
    feed_cfg = feeds_cfg.get(variant, {"key": "feed.xml", "title_suffix": ""})
    feed_key = feed_cfg.get("key", "feed.xml")
    title = podcast.get("title", "Podcast") + feed_cfg.get("title_suffix", "")
    site_link = podcast.get("link") or public_url("")
    image_url = public_url(podcast.get("image_key", "cover.png"))
    now = now or datetime.now(timezone.utc)

    items: list[str] = []
    for ep in sorted(episodes, key=lambda e: e.get("date", ""), reverse=True):
        audio = _pick_audio(ep, variant)
        if not audio or not audio.get("key"):
            continue
        url = public_url(audio["key"])
        ep_title = ep.get("title") or f"{podcast.get('title', 'Episode')} — {ep.get('date')}"
        desc = ep.get("description") or ""
        published = ep.get("published_at") or f"{ep.get('date')}T06:00:00+00:00"
        guid = f"{ep.get('date')}-{variant}"
        extras = ""
        if ep.get("newsletter_key"):
            extras += f'<p><a href="{escape(public_url(ep["newsletter_key"]))}">Read the newsletter</a></p>'
        links = "".join(f'<li><a href="{escape(p.get("url", ""))}">{escape(p.get("title", ""))}</a></li>' for p in ep.get("deep_dive_picks", []) if p.get("url"))
        if links:
            extras += f"<p><strong>Read these:</strong></p><ul>{links}</ul>"
        items.append(
            "\n".join(
                [
                    "    <item>",
                    f"      <title>{escape(ep_title)}</title>",
                    f"      <description>{_cdata(desc)}</description>",
                    f"      <content:encoded>{_cdata('<p>' + escape(desc) + '</p>' + extras)}</content:encoded>",
                    f"      <pubDate>{_rfc2822(published)}</pubDate>",
                    f'      <guid isPermaLink="false">{escape(guid)}</guid>',
                    f'      <enclosure url="{escape(url)}" length="{int(audio.get("bytes", 0))}" type="audio/mpeg"/>',
                    f"      <itunes:duration>{_hms(float(audio.get('duration_seconds', 0)))}</itunes:duration>",
                    "      <itunes:episodeType>full</itunes:episodeType>",
                    f"      <itunes:explicit>{'true' if podcast.get('explicit') else 'false'}</itunes:explicit>",
                    f"      <itunes:summary>{escape(desc[:3900])}</itunes:summary>",
                    f'      <itunes:image href="{escape(image_url)}"/>',
                    "    </item>",
                ]
            )
        )

    category = podcast.get("category", "Technology")
    sub = podcast.get("subcategory") or ""
    category_xml = (
        f'    <itunes:category text="{escape(category)}">\n      <itunes:category text="{escape(sub)}"/>\n    </itunes:category>'
        if sub
        else f'    <itunes:category text="{escape(category)}"/>'
    )
    channel = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<rss version="2.0" xmlns:itunes="{ITUNES_NS}" xmlns:atom="{ATOM_NS}" xmlns:content="{CONTENT_NS}" xmlns:podcast="{PODCAST_NS}">',
            "  <channel>",
            f'    <atom:link href="{escape(public_url(feed_key))}" rel="self" type="application/rss+xml"/>',
            f"    <title>{escape(title)}</title>",
            f"    <link>{escape(site_link)}</link>",
            f"    <language>{escape(podcast.get('language', 'en-us'))}</language>",
            f"    <copyright>{escape(podcast.get('copyright', ''))}</copyright>",
            f"    <description>{_cdata(podcast.get('description', ''))}</description>",
            f"    <lastBuildDate>{format_datetime(now)}</lastBuildDate>",
            "    <itunes:type>episodic</itunes:type>",
            f"    <itunes:author>{escape(podcast.get('author', ''))}</itunes:author>",
            f"    <itunes:subtitle>{escape(podcast.get('subtitle', ''))}</itunes:subtitle>",
            f"    <itunes:summary>{escape(podcast.get('description', '')[:3900])}</itunes:summary>",
            "    <itunes:owner>",
            f"      <itunes:name>{escape(podcast.get('owner_name', ''))}</itunes:name>",
            f"      <itunes:email>{escape(podcast.get('owner_email', ''))}</itunes:email>",
            "    </itunes:owner>",
            f'    <itunes:image href="{escape(image_url)}"/>',
            f"    <image><url>{escape(image_url)}</url><title>{escape(title)}</title><link>{escape(site_link)}</link></image>",
            category_xml,
            f"    <itunes:explicit>{'true' if podcast.get('explicit') else 'false'}</itunes:explicit>",
            *items,
            "  </channel>",
            "</rss>",
            "",
        ]
    )
    return channel


def load_episodes(storage: Storage) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    for key in storage.list_keys("episodes/"):
        if key.endswith("/episode.json"):
            ep = storage.get_json(key)
            if ep:
                episodes.append(ep)
    return episodes


def rebuild_feeds(settings: Settings, storage: Storage) -> dict[str, str]:
    podcast = settings.load_yaml("podcast")
    episodes = load_episodes(storage)
    urls: dict[str, str] = {}
    for variant in podcast.get("feeds", {"standard": {"key": "feed.xml"}}):
        xml = build_feed(podcast, episodes, variant, storage.public_url)
        key = podcast["feeds"][variant].get("key", "feed.xml")
        urls[variant] = storage.put_bytes(key, xml.encode("utf-8"), "application/rss+xml; charset=utf-8", CACHE_SHORT)
        log.info("Feed rebuilt", variant=variant, key=key, episodes=sum(1 for e in episodes if _pick_audio(e, variant)))
    return urls
