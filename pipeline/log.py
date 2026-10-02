"""Minimal structured logging: one JSON line per event, or key=value in a TTY.

Optional live reporter: when PANEL_URL and PANEL_TOKEN are set, every log payload (plus the
explicit run events the workflow emits: status, script, cost, result) is batched and POSTed to
    POST {PANEL_URL}/api/runs/{run_id}/events   Authorization: Bearer {PANEL_TOKEN}
from a background thread. A panel outage can never fail a run: failures are swallowed, the
batch is dropped after one retry, and the reporter disables itself after repeated failures.

Wire contract (see admin/README.md → "Events contract"):
    {"run": {"run_id", "run_url", "repo", "episode_date", "dry_run", "started_at"},   # optional
     "events": [{"seq": 1, "ts": "...", "type": "log", "level": "info", "event": "...", ...},
                {"seq": 2, "ts": "...", "type": "status", "status": "WRITING"}, ...]}
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_JSON = os.environ.get("LOG_FORMAT", "").lower() == "json" or os.environ.get("GITHUB_ACTIONS") == "true"

_root = logging.getLogger("cw")
if not _root.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _root.addHandler(handler)
    _root.setLevel(getattr(logging, _LEVEL, logging.INFO))
    _root.propagate = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── panel reporter ──────────────────────────────────────────────────────────────
Transport = Callable[[str, dict[str, str], dict[str, Any]], int]  # (url, headers, body) -> HTTP status


def _httpx_transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> int:
    import httpx  # imported lazily so the log module stays dependency-free when the reporter is off

    resp = httpx.post(url, headers=headers, json=body, timeout=8.0)
    return resp.status_code


def default_run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"local-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


class PanelReporter:
    """Batches events and ships them to the admin panel. Every public method is fail-safe."""

    MAX_QUEUE = 5000  # events kept in memory while the panel is unreachable

    def __init__(
        self,
        url: str,
        token: str,
        run_id: Optional[str] = None,
        *,
        batch_size: int = 50,
        flush_interval: float = 2.0,
        max_failures: int = 5,
        transport: Optional[Transport] = None,
        start_thread: bool = True,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.run_id = run_id or default_run_id()
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_failures = max_failures
        self.transport: Transport = transport or _httpx_transport
        self.enabled = bool(self.url and self.token)
        self.run_meta: dict[str, Any] = {"run_id": self.run_id, "started_at": _now()}
        server, repo = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY")
        if repo:
            self.run_meta["repo"] = repo
        if server and repo and os.environ.get("GITHUB_RUN_ID"):
            self.run_meta["run_url"] = f"{server}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        self._queue: list[dict[str, Any]] = []
        self._seq = 0
        self._failures = 0
        self._meta_dirty = True
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.sent = 0
        self.dropped = 0
        if self.enabled and start_thread:
            self._thread = threading.Thread(target=self._loop, name="panel-reporter", daemon=True)
            self._thread.start()
            atexit.register(self.close)

    @property
    def endpoint(self) -> str:
        return f"{self.url}/api/runs/{self.run_id}/events"

    # ── producers ────────────────────────────────────────────────────────────
    def set_run_meta(self, **meta: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.run_meta.update({k: v for k, v in meta.items() if v is not None})
            self._meta_dirty = True

    def emit(self, payload: dict[str, Any]) -> None:
        """Enqueue a log payload (already has ts/level/event)."""
        self._enqueue({"type": "log", **payload})

    def event(self, type_: str, **data: Any) -> None:
        """Enqueue an explicit run event: status, script, cost, result."""
        self._enqueue({"ts": _now(), "type": type_, **data})

    def _enqueue(self, item: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                self._seq += 1
                item = {"seq": self._seq, **item}
                if len(self._queue) >= self.MAX_QUEUE:
                    self._queue.pop(0)
                    self.dropped += 1
                self._queue.append(item)
                should_wake = len(self._queue) >= self.batch_size
            if should_wake:
                self._wake.set()
        except Exception:
            pass

    # ── shipping ─────────────────────────────────────────────────────────────
    def flush(self) -> bool:
        """Send everything queued right now. Returns True if the panel accepted it."""
        if not self.enabled:
            return False
        with self._lock:
            if not self._queue and not self._meta_dirty:
                return True
            batch = self._queue[: self.batch_size * 4]
            body: dict[str, Any] = {"events": batch}
            if self._meta_dirty:
                body["run"] = dict(self.run_meta)
        ok = self._post(body)
        with self._lock:
            if ok:
                del self._queue[: len(batch)]
                self._meta_dirty = False
                self.sent += len(batch)
            else:
                # one retry happens on the next tick; after max_failures we drop the batch
                if self._failures >= self.max_failures:
                    del self._queue[: len(batch)]
                    self.dropped += len(batch)
        return ok

    def _post(self, body: dict[str, Any]) -> bool:
        try:
            status = self.transport(self.endpoint, {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, body)
        except Exception as e:  # network down, DNS, timeout, anything
            status = -1
            reason = str(e)[:120]
        else:
            reason = f"HTTP {status}"
        if 200 <= status < 300:
            self._failures = 0
            return True
        self._failures += 1
        if self._failures == self.max_failures:
            _root.warning(json.dumps({"ts": _now(), "level": "warning", "event": "Panel reporter disabled after repeated failures", "reason": reason}) if _JSON else f"[warning] Panel reporter disabled after repeated failures reason={reason}")
            self.enabled = False
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            try:
                while self.enabled:
                    self.flush()
                    with self._lock:
                        if len(self._queue) < self.batch_size:
                            break
            except Exception:
                pass

    def close(self) -> None:
        """Flush what is left (called at exit). Never raises."""
        try:
            self._stop.set()
            self._wake.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=self.flush_interval + 1)
            deadline = time.time() + 10
            while self.enabled and time.time() < deadline:
                with self._lock:
                    pending = bool(self._queue) or self._meta_dirty
                if not pending or not self.flush():
                    break
        except Exception:
            pass


class _NullReporter(PanelReporter):
    def __init__(self):
        super().__init__("", "", "disabled", start_thread=False)
        self.enabled = False


def _reporter_from_env() -> PanelReporter:
    url, token = os.environ.get("PANEL_URL", "").strip(), os.environ.get("PANEL_TOKEN", "").strip()
    if url and token:
        return PanelReporter(url, token)
    return _NullReporter()


panel: PanelReporter = _reporter_from_env()


def set_panel_reporter(reporter: PanelReporter) -> PanelReporter:
    """Swap the process-wide reporter (tests, or a CLI that wants its own run id)."""
    global panel
    panel = reporter
    return panel


# ── logger ──────────────────────────────────────────────────────────────────────
class Logger:
    def __init__(self, **ctx: Any):
        self.ctx = ctx

    def bind(self, **ctx: Any) -> "Logger":
        return Logger(**{**self.ctx, **ctx})

    def _emit(self, level: int, event: str, **kw: Any) -> None:
        if not _root.isEnabledFor(level):
            return
        payload = {"ts": _now(), "level": logging.getLevelName(level).lower(), "event": event, **self.ctx, **kw}
        if _JSON:
            _root.log(level, json.dumps(payload, default=str, ensure_ascii=False))
        else:
            extras = " ".join(f"{k}={_fmt(v)}" for k, v in {**self.ctx, **kw}.items())
            _root.log(level, f"[{payload['level']:<7}] {event} {extras}".rstrip())
        if panel.enabled:
            panel.emit(json.loads(json.dumps(payload, default=str, ensure_ascii=False)))

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
