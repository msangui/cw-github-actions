"""Headline memory for cross-episode dedup. Replaces the `headlines` + `memory_entries`
(pgvector) tables with a single JSON document in storage: state/memory.json.

Entries: {"ns": "headlines"|"aisle_headlines", "date": "YYYY-MM-DD", "title", "url", "embedding"?}
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any, Optional

from pipeline.log import get_logger
from pipeline.storage import Storage

log = get_logger(component="memory")

MEMORY_KEY = "state/memory.json"
MAX_WINDOW_DAYS = 45  # hard prune; per-namespace queries apply their own window


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-\.']*")


def title_words(title: str) -> set[str]:
    """Lower-cased words with punctuation stripped, so 'today,' == 'today'."""
    return set(_WORD_RE.findall(title.lower()))


def title_overlap(a: str, b: str) -> float:
    wa, wb = title_words(a), title_words(b)
    if not wa:
        return 0.0
    return len(wa & wb) / len(wa)


def cosine_distance(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


class HeadlineMemory:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._entries: list[dict[str, Any]] = []
        self._loaded = False
        self._dirty = False

    def load(self) -> None:
        if self._loaded:
            return
        data = self.storage.get_json(MEMORY_KEY) or {}
        self._entries = list(data.get("entries", []))
        self._loaded = True
        log.info("Loaded headline memory", entries=len(self._entries))

    def recent(self, namespace: str, window_days: int, today: Optional[date] = None) -> list[dict[str, Any]]:
        self.load()
        today = today or date.today()
        cutoff = (today - timedelta(days=window_days)).isoformat()
        return [e for e in self._entries if e.get("ns") == namespace and e.get("date", "") >= cutoff]

    def was_covered(self, story: dict[str, Any], namespace: str, window_days: int, threshold: float, today: Optional[date] = None) -> bool:
        """Layer 1: exact URL match or title word-overlap against past episodes."""
        url = story.get("url", "")
        title = story.get("title", "")
        for h in self.recent(namespace, window_days, today):
            if url and h.get("url") == url:
                return True
            if title and title_overlap(title, h.get("title", "")) >= threshold:
                return True
        return False

    def is_semantic_duplicate(self, vec: list[float], namespace: str, window_days: int, max_distance: float, today: Optional[date] = None) -> bool:
        """Layer 2: cosine distance against stored embeddings (was pgvector `<=>`)."""
        for h in self.recent(namespace, window_days, today):
            emb = h.get("embedding")
            if emb and cosine_distance(vec, emb) < max_distance:
                return True
        return False

    def add(self, namespace: str, episode_date: str, stories: list[dict[str, Any]]) -> int:
        self.load()
        existing = {(e.get("ns"), e.get("url") or e.get("title")) for e in self._entries}
        added = 0
        for s in stories:
            key = (namespace, s.get("url") or s.get("title"))
            if key in existing or not s.get("title"):
                continue
            entry: dict[str, Any] = {"ns": namespace, "date": episode_date, "title": s["title"], "url": s.get("url", ""), "source": s.get("source_name", "")}
            if s.get("embedding"):
                entry["embedding"] = [round(float(x), 6) for x in s["embedding"]]
            self._entries.append(entry)
            existing.add(key)
            added += 1
        if added:
            self._dirty = True
        return added

    def save(self, today: Optional[date] = None) -> None:
        if not self._dirty:
            return
        today = today or date.today()
        cutoff = (today - timedelta(days=MAX_WINDOW_DAYS)).isoformat()
        self._entries = [e for e in self._entries if e.get("date", "") >= cutoff]
        self.storage.put_json(MEMORY_KEY, {"entries": self._entries}, cache_control="private, no-store")
        self._dirty = False
        log.info("Saved headline memory", entries=len(self._entries))
