"""Stitch: concat line MP3s, mix the intro jingle with a duck curve, loudnorm to -16 LUFS."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(stage="stitch")


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg error", stderr=result.stderr[-600:])
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-300:]}")


def duck_expression(duck_start_s: float, duck_end_s: float, floor: float) -> str:
    """Per-frame volume expression: 1.0 until duck_start, linear ramp to `floor` by duck_end."""
    ramp = max(duck_end_s - duck_start_s, 0.001)
    drop = 1.0 - floor
    return f"if(lt(t,{duck_start_s}),1,if(lt(t,{duck_end_s}),1-{drop}*(t-{duck_start_s})/{ramp},{floor}))"


def combine_for_extended(standard_line_files: list[str], aisle_line_files: list[str], read_these_start: int) -> list[str]:
    """Insert The Aisle between the last story and 'Read These'."""
    return standard_line_files[:read_these_start] + aisle_line_files + standard_line_files[read_these_start:]


def probe_duration(path: Path | str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def stitch(settings: Settings, line_files: list[str], episode_date: str, output_dir: Path, suffix: str = "", skip_intro: bool = False) -> str:
    cfg = settings.load_yaml("stitch")
    tts_delay_ms = int(cfg.get("tts_delay_ms", 12000))
    duck_start = float(cfg.get("duck_start_s", 9))
    duck_end = float(cfg.get("duck_end_s", 15))
    floor = float(cfg.get("intro_volume_floor", 0.25))
    loudnorm = f"loudnorm=I={float(cfg.get('loudnorm_i', -16))}:TP={float(cfg.get('loudnorm_tp', -1.5))}:LRA={float(cfg.get('loudnorm_lra', 11))}"

    if not line_files:
        log.warning("No line files to stitch")
        return ""
    output_dir.mkdir(parents=True, exist_ok=True)
    concat_list = output_dir / f"concat{suffix}.txt"
    concat_path = output_dir / f"concat{suffix}.mp3"
    premix_path = output_dir / f"premix{suffix}.mp3"
    final_path = output_dir / f"episode_{episode_date}{suffix}.mp3"

    log.info("Step 1/3: concat", lines=len(line_files), suffix=suffix or "(standard)")
    with concat_list.open("w", encoding="utf-8") as f:
        for lf in line_files:
            safe = str(Path(lf).resolve()).replace("'", r"'\''")
            f.write(f"file '{safe}'\n")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(concat_path)])

    intro = settings.intro_mp3_path
    use_intro = intro.exists() and not skip_intro
    log.info("Step 2/3: mix", intro=str(intro) if use_intro else "none", tts_delay_ms=tts_delay_ms)
    if use_intro:
        _run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(intro),
                "-i", str(concat_path),
                "-filter_complex",
                f"[0:a]volume='{duck_expression(duck_start, duck_end, floor)}':eval=frame[intro];"
                f"[1:a]adelay={tts_delay_ms}|{tts_delay_ms}[tts];"
                "[intro][tts]amix=inputs=2:duration=longest:dropout_transition=0[out]",
                "-map", "[out]", "-q:a", "2", str(premix_path),
            ]
        )
    else:
        shutil.copy2(concat_path, premix_path)
    if not premix_path.exists() or premix_path.stat().st_size == 0:
        raise RuntimeError(f"premix{suffix}.mp3 is empty")

    log.info("Step 3/3: loudnorm", filter=loudnorm)
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(premix_path), "-af", loudnorm, "-q:a", "2", str(final_path)])
    size = final_path.stat().st_size if final_path.exists() else 0
    log.info("Stitch complete", output=str(final_path), bytes=size, seconds=round(probe_duration(final_path), 1))
    return str(final_path)


def stitch_extended(settings: Settings, standard_line_files: list[str], aisle_line_files: list[str], read_these_start: int, episode_date: str, output_dir: Path) -> str:
    combined = combine_for_extended(standard_line_files, aisle_line_files, read_these_start)
    return stitch(settings, combined, episode_date, output_dir, suffix="_aisle")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

