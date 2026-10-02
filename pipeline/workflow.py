"""DailyEpisodeWorkflow, single-process edition.

Status machine (same as the Temporal original):
  INGESTING → CURATING → WRITING → EDITING → GENERATING_AUDIO → STITCHING → PUBLISHING
  → PUBLISHED | SAFE_MODE | FAILED

Each stage output is checkpointed so a re-run of the same date resumes where it failed.
"""

from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.checkpoint import Checkpoints
from pipeline.config import Settings
from pipeline.llm import LLM
from pipeline.log import get_logger, panel
from pipeline.memory import HeadlineMemory
from pipeline.notify import esc, telegram
from pipeline.stages import cfo, ingest
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
        self.llm = LLM(settings, log_dir=self.out_dir / "llm_logs")
        self.ckpt = Checkpoints(self.storage, episode_date, self.out_dir, fresh=settings.fresh)
        self.log = log.bind(date=episode_date)
        panel.set_run_meta(episode_date=episode_date, dry_run=settings.dry_run, skip_aisle=settings.skip_aisle, fresh=settings.fresh, run_url=_run_url())
        self.status_log: list[dict[str, str]] = []
        self.llm_calls: list[tuple[str, str | None, dict[str, int]]] = []
        self.serper_queries = 0
        self.tts_chars = 0

    # ── helpers ────────────────────────────────────────────────────────────
    def set_status(self, status: str) -> None:
        self.status_log.append({"status": status, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.log.info("STATUS", status=status)
        panel.event("status", status=status)
        (self.out_dir / "status.json").write_text(json.dumps({"date": self.date, "status": status, "history": self.status_log}, indent=2), encoding="utf-8")

    def stage(self, name: str, fn, *args, **kwargs):
        cached = self.ckpt.load(name)
        if cached is not None:
            return cached
        result = fn(*args, **kwargs)
        self.ckpt.save(name, result)
        return result

    def _track(self, agent: str, result: dict[str, Any]) -> None:
        usage = result.get("token_usage") or {}
        if usage:
            self.llm_calls.append((agent, result.get("model"), usage))

    def _write_text(self, name: str, text: str) -> None:
        (self.out_dir / name).write_text(text, encoding="utf-8")

    # ── main ───────────────────────────────────────────────────────────────
    def run(self) -> dict[str, Any]:
        result = self._run_guarded()
        panel.event("result", **{k: v for k, v in result.items() if k in ("status", "date", "title", "word_count", "cost", "audio_url", "extended_audio_url", "newsletter_url", "error", "has_aisle_variant")})
        panel.close()
        return result

    def _run_guarded(self) -> dict[str, Any]:
        s = self.settings
        existing = self.storage.get_json(f"episodes/{self.date}/episode.json")
        if existing and existing.get("status") == "PUBLISHED" and not s.fresh and not s.dry_run:
            self.log.info("Episode already published; nothing to do (use --fresh to regenerate)")
            return {"status": "ALREADY_PUBLISHED", "date": self.date, "episode": existing}

        try:
            spent = cfo.check_budget_before_run(s, self.storage)
            self.log.info("Budget check passed", month_spent_usd=spent)
        except cfo.BudgetPaused as e:
            self.log.error("Budget hard-pause active — refusing to run", reason=str(e))
            telegram(s, f"⛔ <b>Context Window paused</b>\n{esc(str(e))}\nSet CW_IGNORE_BUDGET=1 to override.")
            return {"status": "PAUSED", "date": self.date, "error": str(e)}

        try:
            return self._run_inner()
        except Exception as e:
            self.log.error("Workflow failed", error=str(e), trace=traceback.format_exc()[-1500:])
            self.set_status("FAILED")
            telegram(s, f"🔴 <b>Context Window {esc(self.date)} FAILED</b>\n<code>{esc(str(e)[:800])}</code>" + (f"\n{esc(_run_url() or '')}" if _run_url() else ""))
            return {"status": "FAILED", "date": self.date, "error": str(e)}

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
        self._report_source_health()
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
        panel.event("script", script=approved_script, title=metadata.get("title", ""), word_count=len(approved_script.split()), hallucinations=editor_result.get("hallucinations", []), changes=len(editor_result.get("changes", [])))

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
        self._notify_published(metadata, brief, result, cost, alerts, extended is not None)
        return {"status": "PUBLISHED", "date": self.date, "has_aisle_variant": extended is not None, "cost": cost, **result}

    def _report_source_health(self) -> None:
        """Write source_health.{json,md} next to the other run artifacts and warn when a Tier 0 feed is silent."""
        rows = ingest.health_report()
        if not rows:
            return  # ingest came from a checkpoint
        window = int(self.settings.load_yaml("sources").get("window_hours", 48))
        self._write_text("source_health.json", json.dumps(rows, indent=2))
        self._write_text("source_health.md", ingest.health_markdown(rows, window))
        bad = [r for r in rows if r["verdict"] != "OK"]
        self.log.info("Source health", feeds=len(rows), unhealthy=len(bad), dead=sum(1 for r in bad if r["verdict"] == "DEAD"))
        t0 = ingest.tier0_problems(rows)
        if t0:
            names = ", ".join(f"{r['source']} ({r['verdict']})" for r in t0)
            self.log.warning("Tier 0 sources delivered nothing", sources=names)
            telegram(self.settings, f"⚠️ <b>Context Window {esc(self.date)}</b> — Tier 0 feeds silent: {esc(names)}")

    # ── safe mode ──────────────────────────────────────────────────────────
    def _safe_mode(self, brief: dict, aisle_brief: dict, script_result: dict, editor_result: dict, approved_aisle_script: Optional[str]) -> dict[str, Any]:
        s = self.settings
        self.set_status("SAFE_MODE")
        self._write_text("script_rejected.txt", script_result.get("script", ""))
        panel.event("script", script=script_result.get("script", ""), title=script_result.get("metadata", {}).get("title", ""), word_count=len(script_result.get("script", "").split()), hallucinations=editor_result.get("hallucinations", []), rejected=True)
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
        headlines = "\n".join(f"• {esc(st['title'])}" for st in brief["stories"][:14])
        hall = "\n".join(f"• {esc(h)}" for h in editor_result.get("hallucinations", [])[:8])
        telegram(s, f"🟠 <b>Context Window {esc(self.date)} — SAFE MODE</b>\nEditor rejected the script. No main audio published.\n\n<b>Flagged:</b>\n{hall or '(none listed)'}\n\n<b>Headlines:</b>\n{headlines}")
        return {"status": "SAFE_MODE", "date": self.date, "headlines": [st["title"] for st in brief["stories"]], "has_aisle_variant": extended is not None, "cost": cost}

    # ── cost / notify ──────────────────────────────────────────────────────
    def _cost(self) -> dict[str, Any]:
        cost = cfo.estimate_cost(self.settings, self.llm_calls, self.tts_chars, self.serper_queries)
        panel.event("cost", cost=cost)
        return cost

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


def run_episode(settings: Settings, episode_date: str) -> dict[str, Any]:
    return EpisodeRun(settings, episode_date).run()
