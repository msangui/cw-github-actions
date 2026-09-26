"""OpenAI text-embedding-3-small (1536-d) for semantic headline dedup. Optional."""

from __future__ import annotations

from typing import Optional

import httpx

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(component="embeddings")

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
MODEL = "text-embedding-3-small"


def embed_text(settings: Settings, text: str) -> Optional[list[float]]:
    if not settings.openai_api_key:
        return None
    try:
        resp = httpx.post(
            OPENAI_EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={"model": MODEL, "input": text},
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as e:
        log.warning("Embedding failed", text_preview=text[:50], error=str(e)[:200])
        return None
