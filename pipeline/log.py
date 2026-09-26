"""Minimal structured logging: one JSON line per event, or key=value in a TTY."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_JSON = os.environ.get("LOG_FORMAT", "").lower() == "json" or os.environ.get("GITHUB_ACTIONS") == "true"

_root = logging.getLogger("cw")
if not _root.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _root.addHandler(handler)
    _root.setLevel(getattr(logging, _LEVEL, logging.INFO))
    _root.propagate = False


class Logger:
    def __init__(self, **ctx: Any):
        self.ctx = ctx

    def bind(self, **ctx: Any) -> "Logger":
        return Logger(**{**self.ctx, **ctx})

    def _emit(self, level: int, event: str, **kw: Any) -> None:
        if not _root.isEnabledFor(level):
            return
        payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": logging.getLevelName(level).lower(), "event": event, **self.ctx, **kw}
        if _JSON:
            _root.log(level, json.dumps(payload, default=str, ensure_ascii=False))
        else:
            extras = " ".join(f"{k}={_fmt(v)}" for k, v in {**self.ctx, **kw}.items())
            _root.log(level, f"[{payload['level']:<7}] {event} {extras}".rstrip())

    def debug(self, event: str, **kw: Any) -> None:
        self._emit(logging.DEBUG, event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        self._emit(logging.INFO, event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._emit(logging.WARNING, event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._emit(logging.ERROR, event, **kw)


def _fmt(v: Any) -> str:
    s = str(v)
    if " " in s or s == "":
        return json.dumps(s, ensure_ascii=False)
    return s


def get_logger(**ctx: Any) -> Logger:
    return Logger(**ctx)
