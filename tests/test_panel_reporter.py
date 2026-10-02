"""PanelReporter: batching, bearer auth, and the guarantee that a panel outage never fails a run."""

import json

import pipeline.log as logmod
from pipeline.log import PanelReporter, get_logger, set_panel_reporter


class FakeTransport:
    def __init__(self, statuses=None, raise_exc=None):
        self.calls: list[tuple[str, dict, dict]] = []
        self.statuses = list(statuses or [])
        self.raise_exc = raise_exc

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(json.dumps(body))))
        if self.raise_exc:
            raise self.raise_exc
        return self.statuses.pop(0) if self.statuses else 202

    @property
    def events(self):
        return [e for _, _, b in self.calls for e in b["events"]]


def _reporter(transport, **kw):
    return PanelReporter("https://panel.example.com/", "secret-token", "12345", transport=transport, start_thread=False, **kw)


def test_disabled_when_env_missing(monkeypatch):
    monkeypatch.delenv("PANEL_URL", raising=False)
    monkeypatch.delenv("PANEL_TOKEN", raising=False)
    r = logmod._reporter_from_env()
    assert not r.enabled
    r.emit({"event": "x"})
    r.event("status", status="WRITING")
    assert r.flush() is False


def test_events_are_batched_with_bearer_and_run_meta():
    t = FakeTransport()
    r = _reporter(t)
    r.set_run_meta(episode_date="2026-09-27", dry_run=True)
    r.emit({"ts": "t", "level": "info", "event": "hello", "stage": "writer"})
    r.event("status", status="WRITING")
    r.event("cost", cost={"total_usd": 1.5})
    assert r.flush() is True
    assert len(t.calls) == 1
    url, headers, body = t.calls[0]
    assert url == "https://panel.example.com/api/runs/12345/events"
    assert headers["Authorization"] == "Bearer secret-token"
    assert body["run"]["run_id"] == "12345" and body["run"]["episode_date"] == "2026-09-27" and body["run"]["dry_run"] is True
    assert [e["seq"] for e in body["events"]] == [1, 2, 3]
    assert [e["type"] for e in body["events"]] == ["log", "status", "cost"]
    assert body["events"][1]["status"] == "WRITING"
    # run meta only travels once unless it changes
    r.emit({"event": "again"})
    r.flush()
    assert "run" not in t.calls[1][2]
    r.set_run_meta(status="DONE")
    r.emit({"event": "again2"})
    r.flush()
    assert t.calls[2][2]["run"]["status"] == "DONE"


def test_transport_exceptions_never_propagate_and_retry_then_drop():
    t = FakeTransport(raise_exc=ConnectionError("boom"))
    r = _reporter(t, max_failures=3)
    r.emit({"event": "one"})
    for _ in range(3):
        assert r.flush() is False
    assert r.enabled is False  # disabled after max_failures
    assert r.dropped == 1 and r.sent == 0
    # everything after that is a no-op
    r.emit({"event": "two"})
    assert r.flush() is False
    assert len(t.calls) == 3


def test_http_errors_keep_batch_for_one_retry_then_succeed():
    t = FakeTransport(statuses=[500, 202])
    r = _reporter(t)
    r.emit({"event": "one"})
    assert r.flush() is False
    assert r.flush() is True
    assert r.sent == 1 and len(t.events) == 2  # same event sent twice, seq identical
    assert t.events[0]["seq"] == t.events[1]["seq"] == 1


def test_queue_is_bounded():
    t = FakeTransport(raise_exc=ConnectionError("down"))
    r = _reporter(t, max_failures=999)
    r.MAX_QUEUE = 10
    for i in range(25):
        r.emit({"event": f"e{i}"})
    assert len(r._queue) == 10 and r.dropped == 15
    assert r._queue[0]["event"] == "e15"


def test_logger_feeds_reporter_and_round_trips_json():
    t = FakeTransport()
    prev = logmod.panel
    r = set_panel_reporter(_reporter(t))
    try:
        get_logger(component="test").info("Hello panel", n=3, path=__import__("pathlib").Path("/tmp/x"))
        r.flush()
    finally:
        set_panel_reporter(prev)
    ev = t.events[0]
    assert ev["type"] == "log" and ev["event"] == "Hello panel" and ev["component"] == "test"
    assert ev["n"] == 3 and ev["path"] == "/tmp/x" and ev["level"] == "info" and "ts" in ev


def test_background_thread_flushes_and_close_drains():
    t = FakeTransport()
    r = PanelReporter("https://p.example", "tok", "1", transport=t, batch_size=2, flush_interval=0.05)
    r.emit({"event": "a"})
    r.emit({"event": "b"})  # hits batch_size -> wakes the thread
    r.emit({"event": "c"})
    r.close()
    assert [e["event"] for e in t.events] == ["a", "b", "c"]
    assert r.sent == 3
