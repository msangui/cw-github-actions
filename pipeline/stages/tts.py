"""TTS: one ElevenLabs call per script line. Idempotent — existing line files (local or
mirrored in <storage>/work/<date>/) are reused, so a re-run only pays for missing lines."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import httpx

from pipeline.config import Settings
from pipeline.log import get_logger
from pipeline.stages.writer import SECTION_MARKER
from pipeline.storage import Storage

log = get_logger(stage="tts")

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


def parse_script_lines(script: str) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Return [(speaker, text), ...] and section markers as line indexes."""
    lines: list[tuple[str, str]] = []
    markers: dict[str, int] = {}
    for raw in script.split("\n"):
        raw = raw.strip()
        if raw == SECTION_MARKER:
            markers["read_these_start"] = len(lines)
        elif raw.startswith("CLAIRE:"):
            text = raw[7:].strip()
            if text:
                lines.append(("CLAIRE", text))
        elif raw.startswith("FLINT:"):
            text = raw[6:].strip()
            if text:
                lines.append(("FLINT", text))
    return lines, markers


def _voice_payload(settings: Settings, speaker: str) -> tuple[str, dict[str, Any]]:
    v = settings.voice(speaker)
    voice_id = v.get("voice_id", "")
    voice_settings = {k: v[k] for k in ("stability", "similarity_boost", "style", "use_speaker_boost") if k in v}
    return voice_id, voice_settings


def _synthesize(client: httpx.Client, api_key: str, voice_id: str, model_id: str, text: str, voice_settings: dict[str, Any], delays: list[float]) -> Optional[bytes]:
    for attempt, delay in enumerate(delays):
        try:
            resp = client.post(
                f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": model_id, "voice_settings": voice_settings},
            )
            if resp.status_code == 200 and resp.content:
                return resp.content
            log.warning("ElevenLabs error", status=resp.status_code, attempt=attempt, body=resp.text[:160])
            if resp.status_code in (401, 402):
                return None  # not going to fix itself
        except Exception as e:
            log.warning("TTS request failed", attempt=attempt, error=str(e)[:160])
        if attempt < len(delays) - 1:
            time.sleep(delay)
    return None


def generate_tts(settings: Settings, storage: Optional[Storage], script: str, episode_date: str, output_dir: Path, subdir: str = "") -> dict[str, Any]:
    voices_cfg = settings.load_yaml("voices")
    model_id = voices_cfg.get("model_id", "eleven_turbo_v2_5")
    gap = float(voices_cfg.get("gap_between_calls_ms", 300)) / 1000.0
    delays = [float(d) for d in voices_cfg.get("retry_delays_seconds", [1, 2, 4])]

    line_dir = output_dir / (subdir or "lines")
    line_dir.mkdir(parents=True, exist_ok=True)
    work_prefix = f"work/{episode_date}/{subdir or 'lines'}"

    lines, markers = parse_script_lines(script)
    total_chars = sum(len(t) for _, t in lines)
    log.info("Parsed script", lines=len(lines), chars=total_chars, markers=markers, subdir=subdir or "lines")

    if not settings.elevenlabs_api_key:
        log.warning("No ELEVENLABS_API_KEY — skipping audio generation")
        return {"line_files": [], "total_chars": total_chars, "section_markers": markers, "generated": 0, "reused": 0, "failed": len(lines), "chars_synthesized": 0}

    line_files: list[str] = []
    generated = reused = failed = 0
    chars_synthesized = 0
    with httpx.Client(timeout=60.0) as client:
        for i, (speaker, text) in enumerate(lines):
            name = f"line_{i:04d}.mp3"
            out_path = line_dir / name
            if not out_path.exists() and storage is not None:
                storage.download(f"{work_prefix}/{name}", out_path)
            if out_path.exists() and out_path.stat().st_size > 0:
                line_files.append(str(out_path))
                reused += 1
                continue

            voice_id, voice_settings = _voice_payload(settings, speaker)
            if not voice_id:
                raise RuntimeError(f"No voice_id configured for {speaker} (config/voices.yaml or {speaker}_VOICE_ID)")
            audio = _synthesize(client, settings.elevenlabs_api_key, voice_id, model_id, text, voice_settings, delays)
            if audio:
                out_path.write_bytes(audio)
                line_files.append(str(out_path))
                generated += 1
                chars_synthesized += len(text)
                if storage is not None:
                    try:
                        storage.put_file(f"{work_prefix}/{name}", out_path, cache_control="private, no-store")
                    except Exception as e:
                        log.warning("Could not mirror line to storage", line=i, error=str(e)[:120])
            else:
                failed += 1
                log.warning("Failed to generate TTS line", line=i, speaker=speaker)
            time.sleep(gap)

    log.info("TTS complete", lines=len(lines), generated=generated, reused=reused, failed=failed, chars=total_chars)
    if failed and failed > len(lines) * 0.05:
        raise RuntimeError(f"TTS failed for {failed}/{len(lines)} lines — refusing to stitch a broken episode")
    return {
        "line_files": line_files,
        "total_chars": total_chars,
        "chars_synthesized": chars_synthesized,
        "section_markers": markers,
        "generated": generated,
        "reused": reused,
        "failed": failed,
    }
