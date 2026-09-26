"""Operator notifications via Telegram. Best-effort: never fails the pipeline."""

from __future__ import annotations

import html

import httpx

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(component="notify")


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def telegram(settings: Settings, text_html: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text_html[:4000], "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15.0,
        )
        if resp.status_code != 200:
            log.warning("Telegram send failed", status=resp.status_code, body=resp.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("Telegram send error", error=str(e))
        return False
