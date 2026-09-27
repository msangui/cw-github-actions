"""DailyEpisodeWorkflow, single-process edition.

Status machine (same as the Temporal original):
  INGESTING → CURATING → WRITING → EDITING → GENERATING_AUDIO → STITCHING → PUBLISHING
  → PUBLISHED | SAFE_MODE | FAILED

Each stage output is checkpointed so a re-run of the same date resumes where it failed.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.analytics import Analytics, sanitize_agent_overrides
from pipeline.checkpoint import Checkpoints
from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger
from pipeline.memory import HeadlineMemory
from pipeline.notify import esc, telegram
from pipeline.stages import cfo
from pipeline.stages.aisle_curator import curate_aisle
from pipeline.stages.aisle_writer import write_aisle
from pipeline.stages.coverage import add_coverage
from pipeline.stages.curator import curate
from pipeline.stages.editor import edit_script
from pipeline.stages.ingest import ingest_aisle, ingest_main
from pipeline.stages.newsletter import generate_newsletter
from pipeline.stages.publish import publish_episode
from pipeline.stages.stitch import ffmpeg_available, stitch, stitch_extended
from pipeline.stages.tts import generate_tts
from pipeline.stages.writer import write_script
from pipeline.storage import Storage

log = get_logger(component="workflow")


def _run_url() -> Optional[str]:
    server, repo, run_id = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


class EpisodeRun:
    def __init__(self, settings: Settings, episode_date: str):
        self.settings = settings
        self.date = episode_date
        self.out_dir: Path = settings.output_dir / episode_date
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.storage = Storage(settings)
        self.memory = HeadlineMemory(self.storage)
        self.analytics = Analytics(settings, episode_date)
        self.llm = LLM(settings, log_dir=self.out_dir / "llm_logs", analytics=self.analytics)
        self.ckpt = Checkpoints(self.storage, episode_date, self.out_dir, fresh=settings.fresh)
        self.log = log.bind(date=episode_date)
        self.status_log: list[dict[str, str]] = []
        self.llm_calls: list[tuple[str, str | None, dict[str, int]]] = []
        self.serper_queries = 0
        self.tts_chars = 0
        self.started = time.time()
        self._status_since = self.started
        self._status: Optional[str] = None

    # ── helpers ────────────────────────────────────────────────────────────
    def set_status(self, status: str) -> None:
        now = time.time()
        self.status_log.append({"status": status, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.log.info("STATUS", status=status)
        (self.out_dir / "status.json").write_text(json.dumps({"date": self.date, "status": status, "history": self.status_log}, indent=2), encoding="utf-8")
        self.analytics.capture("episode_status_changed", status=status, previous_status=self._status, seconds_in_previous=round(now - self._status_since, 1))
        self._status, self._status_since = status, now

    def stage(self, name: str, fn, *args, **kwargs):
        cached = self.ckpt.load(name)
        if cached is not None:
            self.analytics.capture("pipeline_stage_completed", stage=name, seconds=0.0, from_checkpoint=True)
            return cached
        started = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            seconds = round(time.time() - started, 1)
            self.analytics.capture("pipeline_stage_failed", stage=name, seconds=seconds, error=f"{type(e).__name__}: {str(e)[:300]}")
            self.analytics.span(name, seconds, stage=name, **{"$ai_is_error": True, "$ai_error": f"{type(e).__name__}: {str(e)[:300]}"})
            if not hasattr(e, "cw_stage"):
                try:
                    e.cw_stage = name  # type: ignore[attr-defined]
                except Exception:
                    pass
            raise
        seconds = round(time.time() - started, 1)
        self.ckpt.save(name, result)
        self.analytics.capture("pipeline_stage_completed", stage=name, seconds=seconds, from_checkpoint=False)
        self.analytics.span(name, seconds, stage=name)
        return result

    def _apply_feature_flags(self) -> Optional[str]:
        """Evaluate the PostHog operational flags once. Returns a pause reason, or None to proceed."""
        a, s = self.analytics, self.settings
        if not a.enabled:
            return None
        a.load_flags()
        if _flag_on(a.flag("paused", False)):
            return f"Feature flag `{a.flag_key('paused')}` is on"
        applied: dict[str, Any] = {}
        if a.flag("aisle_enabled", True) is False and not s.skip_aisle:
            s.skip_aisle = applied["skip_aisle"] = True
        if _flag_on(a.flag("force_dry_run", False)) and not s.dry_run:
            s.dry_run = applied["dry_run"] = True
        overrides = sanitize_agent_overrides(a.payload("agent_overrides"))
        if overrides:
            s.agent_overrides = overrides
            applied["agent_overrides"] = overrides
        if applied:
            self.log.info("Feature flags applied", **applied)
        return None

    def _track(self, agent: str, result: dict[str, Any]) -> None:
        usage = result.get("token_usage") or {}
        if usage:
            self.llm_calls.append((agent, result.get("model"), usage))

    def _write_text(self, name: str, text: str) -> None:
        (self.out_dir / name).write_text(text, encoding="utf-8")

    # ── main ───────────────────────────────────────────────────────────────
    def run(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "FAILED", "date": self.date}
        try:
            result = self._run_guarded()
            return result
        finally:
            self._finish(result)

    def _run_guarded(self) -> dict[str, Any]:
        s = self.settings
        existing = self.storage.get_json(f"episodes/{self.date}/episode.json")
        if existing and existing.get("status") == "PUBLISHED" and not s.fresh and not s.dry_run:
            self.log.info("Episode already published; nothing to do (use --fresh to regenerate)")
            return {"status": "ALREADY_PUBLISHED", "date": self.date, "episode": existing}

        pause_reason = self._apply_feature_flags()
        if pause_reason:
            self.log.error("Paused by feature flag — refusing to run", reason=pause_reason)
            self.analytics.capture("episode_paused", reason="feature_flag", detail=pause_reason)
            telegram(s, f"⛔ <b>Context Window paused</b>\n{esc(pause_reason)}\nTurn the flag off in PostHog to resume.")
            return {"status": "PAUSED", "date": self.date, "error": pause_reason}

        try:
            spent = cfo.check_budget_before_run(s, self.storage)
            self.log.info("Budget check passed", month_spent_usd=spent)
        except cfo.BudgetPaused as e:
            self.log.error("Budget hard-pause active — refusing to run", reason=str(e))
            self.analytics.capture("episode_paused", reason="budget", detail=str(e))
            telegram(s, f"⛔ <b>Context Window paused</b>\n{esc(str(e))}\nSet CW_IGNORE_BUDGET=1 to override.")
            return {"status": "PAUSED", "date": self.date, "error": str(e)}

        self.analytics.capture("episode_run_started", month_spent_usd=spent, resuming=bool(self.storage.list_keys(f"work/{self.date}/checkpoints/")) and not s.fresh)
        try:
            return self._run_inner()
        except Exception as e:
            self.log.error("Workflow failed", error=str(e), trace=traceback.format_exc()[-1500:])
            self.analytics.exception(e, status=self._status, stage=getattr(e, "cw_stage", None))
            self.set_status("FAILED")
            telegram(s, f"🔴 <b>Context Window {esc(self.date)} FAILED</b>\n<code>{esc(str(e)[:800])}</code>" + (f"\n{esc(_run_url() or '')}" if _run_url() else ""))
            return {"status": "FAILED", "date": self.date, "error": str(e), "stage": getattr(e, "cw_stage", None)}

    def _finish(self, result: dict[str, Any]) -> None:
        """Final analytics for the run, then flush PostHog before the process exits."""
        try:
            seconds = round(time.time() - self.started, 1)
            status = str(result.get("status", "FAILED"))
            cost = result.get("cost") or {}
            self.analytics.capture("episode_run_finished", status=status, seconds=seconds, error=(result.get("error") or None), **_cost_props(cost))
            self.analytics.trace(status, seconds, output_state={"title": result.get("title"), "cost_usd": cost.get("total_usd"), "error": result.get("error")})
        finally:
            self.analytics.shutdown()

    def _run_inner(self) -> dict[str, Any]:
        s = self.settings
        aisle_enabled = s.load_yaml("sources").get("aisle", {}).get("enabled", True) and not s.skip_aisle

        # 1. Ingest (main + aisle in parallel)
        self.set_status("INGESTING")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_main = pool.submit(self.stage, "ingest_main", ingest_main, s)
            f_aisle = pool.submit(self.stage, "ingest_aisle", ingest_aisle, s) if aisle_enabled else None
            raw_stories: list[dict] = f_main.result()
            raw_aisle: list[dict] = f_aisle.result() if f_aisle else []
        self.log.info("Ingested", main=len(raw_stories), aisle=len(raw_aisle))
        if not raw_stories:
            raise RuntimeError("No stories ingested from any source")

        # 2. Cross-coverage
        cov = self.stage("coverage", lambda: dict(zip(("stories", "queries"), add_coverage(s, raw_stories))))
        stories: list[dict] = cov["stories"]
        self.serper_queries = int(cov.get("queries", 0))

        # 3. Curate
        self.set_status("CURATING")
        brief = self.stage("curator", curate, s, self.llm, self.memory, stories)
        self._track("curator", brief)
        aisle_brief = self.stage("aisle_curator", curate_aisle, s, self.llm, self.memory, raw_aisle) if raw_aisle else {"stories": [], "story_count": 0}
        self._track("aisle_curator", aisle_brief)
        if brief.get("story_count", 0) == 0:
            raise RuntimeError("Curator selected zero stories")
        self._write_text("brief.json", json.dumps(brief, indent=2, ensure_ascii=False, default=str))

        # 4. Write (main + aisle in parallel)
        self.set_status("WRITING")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_script = pool.submit(self.stage, "writer", write_script, s, self.llm, brief)
            f_aisle_script = pool.submit(self.stage, "aisle_writer", write_aisle, s, self.llm, aisle_brief) if aisle_brief.get("story_count", 0) > 0 else None
            script_result = f_script.result()
            aisle_script_result = f_aisle_script.result() if f_aisle_script else None
        self._track("writer", script_result)
        if aisle_script_result:
            self._track("aisle_writer", aisle_script_result)
        self._write_text("script_draft.txt", script_result["script"])

        # 5. Edit (main + aisle in parallel)
        self.set_status("EDITING")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_edit = pool.submit(self.stage, "editor", edit_script, s, self.llm, script_result, stories)
            f_aisle_edit = (
                pool.submit(self.stage, "aisle_editor", edit_script, s, self.llm, aisle_script_result, raw_aisle)
                if aisle_script_result and aisle_script_result.get("script")
                else None
            )
            editor_result = f_edit.result()
            aisle_editor_result = f_aisle_edit.result() if f_aisle_edit else None
        self._track("editor", editor_result)
        if aisle_editor_result:
            self._track("aisle_editor", aisle_editor_result)

        approved_aisle_script = aisle_editor_result["corrected_script"] if aisle_editor_result and aisle_editor_result.get("approved") else None
        metadata = script_result.get("metadata", {})

        if not editor_result.get("approved", False):
            self.log.warning("Editor did not approve script — entering safe mode", hallucinations=editor_result.get("hallucinations"))
            return self._safe_mode(brief, aisle_brief, script_result, editor_result, approved_aisle_script)

        approved_script = editor_result["corrected_script"]
        self._write_text("script.txt", approved_script)

        if s.dry_run:
            self.set_status("DRY_RUN_COMPLETE")
            cost = self._cost()
            self._write_text("cost.json", json.dumps(cost, indent=2))
            self.log.info("Dry run complete — skipping TTS/stitch/publish", cost_usd=cost["total_usd"])
            return {"status": "DRY_RUN", "date": self.date, "title": metadata.get("title"), "word_count": len(approved_script.split()), "cost": cost, "output_dir": str(self.out_dir)}

        # 6. TTS (+ newsletter, + aisle TTS)
        self.set_status("GENERATING_AUDIO")
        listen_url = self.storage.public_url(f"episodes/{self.date}/episode_{self.date}.mp3")
        tts_result = self.stage("tts", generate_tts, s, self.storage, approved_script, self.date, self.out_dir)
        newsletter = self.stage("newsletter", generate_newsletter, s, self.llm, approved_script, brief["stories"], self.date, listen_url)
        self._track("newsletter", newsletter)
        aisle_tts = self.stage("aisle_tts", generate_tts, s, self.storage, approved_aisle_script, self.date, self.out_dir, "aisle") if approved_aisle_script else None
        self.tts_chars = int(tts_result.get("chars_synthesized", 0)) + int((aisle_tts or {}).get("chars_synthesized", 0))
        if not tts_result["line_files"]:
            raise RuntimeError("TTS produced no audio (missing ELEVENLABS_API_KEY or all lines failed)")

        # 7. Stitch
        self.set_status("STITCHING")
        if not ffmpeg_available():
            raise RuntimeError("ffmpeg/ffprobe not found on PATH")
        stitched = self.stage("stitch", stitch, s, tts_result["line_files"], self.date, self.out_dir)
        extended = None
        read_these = (tts_result.get("section_markers") or {}).get("read_these_start")
        if aisle_tts and aisle_tts.get("line_files") and read_these is not None:
            extended = self.stage("stitch_extended", stitch_extended, s, tts_result["line_files"], aisle_tts["line_files"], int(read_these), self.date, self.out_dir)
        elif aisle_tts and aisle_tts.get("line_files"):
            self.log.warning("Aisle audio exists but script has no section marker — extended edition skipped")
        if not Path(stitched).exists():  # checkpoint from a previous runner whose files are gone
            self.ckpt.fresh = True
            stitched = self.stage("stitch", stitch, s, tts_result["line_files"], self.date, self.out_dir)
            if extended and not Path(extended).exists():
                extended = self.stage("stitch_extended", stitch_extended, s, tts_result["line_files"], aisle_tts["line_files"], int(read_these), self.date, self.out_dir)
            self.ckpt.fresh = s.fresh

        # 8. Publish
        self.set_status("PUBLISHING")
        cost = self._cost()
        self._write_text("cost.json", json.dumps(cost, indent=2))
        if s.skip_publish:
            self.set_status("AUDIO_READY")
            return {"status": "AUDIO_READY", "date": self.date, "audio_path": stitched, "extended_audio_path": extended, "cost": cost}

        result = publish_episode(
            s, self.storage, self.memory, self.date, "PUBLISHED", metadata, approved_script, brief["stories"], aisle_brief.get("stories", []),
            stitched, extended, newsletter.get("html"), editor_result.get("hallucinations", []), cost, _run_url(),
        )
        alerts = cfo.record_episode_cost(s, self.storage, self.date, cost)
        self.set_status("PUBLISHED")
        for alert in alerts:
            self.analytics.capture("budget_alert", message=alert, **_cost_props(cost))
        episode_props = {
            "title": metadata.get("title"),
            "story_count": brief.get("story_count", 0),
            "aisle_story_count": aisle_brief.get("story_count", 0),
            "word_count": len(approved_script.split()),
            "has_aisle_variant": extended is not None,
            "hallucinations": len(editor_result.get("hallucinations", [])),
            "editor_changes": len(editor_result.get("changes", [])),
            "audio_duration_seconds": result.get("audio_duration_seconds"),
            "tts_lines_generated": tts_result.get("generated", 0),
            "tts_lines_reused": tts_result.get("reused", 0),
            "audio_url": result.get("audio_url"),
            **_cost_props(cost),
        }
        self.analytics.capture("episode_published", **episode_props)
        self.analytics.identify_episode(status="PUBLISHED", **episode_props)
        self._notify_published(metadata, brief, result, cost, alerts, extended is not None)
        return {"status": "PUBLISHED", "date": self.date, "title": metadata.get("title"), "has_aisle_variant": extended is not None, "cost": cost, **result}

    # ── safe mode ──────────────────────────────────────────────────────────
    def _safe_mode(self, brief: dict, aisle_brief: dict, script_result: dict, editor_result: dict, approved_aisle_script: Optional[str]) -> dict[str, Any]:
        s = self.settings
        self.set_status("SAFE_MODE")
        self._write_text("script_rejected.txt", script_result.get("script", ""))
        extended = None
        if approved_aisle_script and not s.dry_run and ffmpeg_available():
            try:
                aisle_tts = self.stage("aisle_tts", generate_tts, s, self.storage, approved_aisle_script, self.date, self.out_dir, "aisle")
                if aisle_tts.get("line_files"):
                    extended = self.stage("stitch_aisle_only", stitch, s, aisle_tts["line_files"], self.date, self.out_dir, "_aisle", True)
            except Exception as e:
                self.log.warning("Aisle audio failed in safe mode", error=str(e)[:200])
        cost = self._cost()
        if not s.dry_run and not s.skip_publish:
            publish_episode(
                s, self.storage, self.memory, self.date, "SAFE_MODE", script_result.get("metadata", {}), script_result.get("script", ""),
                brief["stories"], aisle_brief.get("stories", []), None, extended, None, editor_result.get("hallucinations", []), cost, _run_url(),
            )
            cfo.record_episode_cost(s, self.storage, self.date, cost)
        safe_props = {
            "title": script_result.get("metadata", {}).get("title"),
            "story_count": brief.get("story_count", 0),
            "hallucinations": len(editor_result.get("hallucinations", [])),
            "flagged": editor_result.get("hallucinations", [])[:8],
            "has_aisle_variant": extended is not None,
            **_cost_props(cost),
        }
        self.analytics.capture("episode_safe_mode", **safe_props)
        self.analytics.identify_episode(status="SAFE_MODE", **safe_props)
        headlines = "\n".join(f"• {esc(st['title'])}" for st in brief["stories"][:14])
        hall = "\n".join(f"• {esc(h)}" for h in editor_result.get("hallucinations", [])[:8])
        telegram(s, f"🟠 <b>Context Window {esc(self.date)} — SAFE MODE</b>\nEditor rejected the script. No main audio published.\n\n<b>Flagged:</b>\n{hall or '(none listed)'}\n\n<b>Headlines:</b>\n{headlines}")
        return {"status": "SAFE_MODE", "date": self.date, "headlines": [st["title"] for st in brief["stories"]], "has_aisle_variant": extended is not None, "cost": cost}

    # ── cost / notify ──────────────────────────────────────────────────────
    def _cost(self) -> dict[str, Any]:
        return cfo.estimate_cost(self.settings, self.llm_calls, self.tts_chars, self.serper_queries)

    def _notify_published(self, metadata: dict, brief: dict, result: dict, cost: dict, alerts: list[str], has_aisle: bool) -> None:
        picks = "\n".join(f"• <a href=\"{esc(p.get('url', ''))}\">{esc(p.get('title', ''))}</a>" for p in metadata.get("deep_dive_picks", [])[:6] if p.get("url"))
        lines = [
            f"🟢 <b>{esc(metadata.get('title') or 'Context Window')}</b> — {esc(self.date)}",
            esc(metadata.get("description", ""))[:400],
            "",
            f"🎧 <a href=\"{esc(result.get('audio_url') or '')}\">Episode MP3</a>" + (f" · <a href=\"{esc(result.get('extended_audio_url'))}\">Extended (The Aisle)</a>" if has_aisle else ""),
            f"📰 <a href=\"{esc(result.get('newsletter_url') or '')}\">Newsletter</a>" if result.get("newsletter_url") else "",
            f"📡 Feed: {esc((result.get('feeds') or {}).get('standard', ''))}",
            "",
            f"<b>Read these:</b>\n{picks}" if picks else "",
            "",
            f"💸 ${cost['total_usd']:.2f} (LLM ${cost['llm_usd']:.2f} · TTS ${cost['tts_usd']:.2f}) · {brief.get('story_count', 0)} stories",
        ]
        if alerts:
            lines.append("⚠️ " + "\n⚠️ ".join(esc(a) for a in alerts))
        telegram(self.settings, "\n".join(ln for ln in lines if ln is not None))


def _flag_on(value: Any) -> bool:
    """PostHog flags are bools or variant strings; any variant counts as on (like `feature_enabled`)."""
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false", "off", "0"}
    return bool(value)


def _cost_props(cost: dict[str, Any]) -> dict[str, Any]:
    if not cost:
        return {}
    return {
        "cost_total_usd": cost.get("total_usd"),
        "cost_llm_usd": cost.get("llm_usd"),
        "cost_tts_usd": cost.get("tts_usd"),
        "cost_serper_usd": cost.get("serper_usd"),
        "cost_llm_by_agent": cost.get("llm_by_agent"),
        "tts_chars": cost.get("tts_chars"),
        "serper_queries": cost.get("serper_queries"),
    }


def run_episode(settings: Settings, episode_date: str) -> dict[str, Any]:
    return EpisodeRun(settings, episode_date).run()
