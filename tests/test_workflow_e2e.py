"""Drives EpisodeRun end to end with local storage, stub LLM (no key) and faked TTS."""

import subprocess
from pathlib import Path

import pytest

from pipeline import workflow
from pipeline.stages.stitch import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")

STORIES = [
    {"title": f"Story number {i} about agents", "url": f"https://example.com/{i}", "summary": "s", "published_at": "2026-09-26T04:00:00+00:00", "source_name": "Src", "source_tier": "0" if i < 2 else "1"}
    for i in range(6)
]


def _fake_tts(settings, storage, script, episode_date, output_dir, subdir=""):
    from pipeline.stages.tts import parse_script_lines

    lines, markers = parse_script_lines(script)
    d = Path(output_dir) / (subdir or "lines")
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i, _ in enumerate(lines):
        p = d / f"line_{i:04d}.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", "-q:a", "6", str(p)], check=True)
        files.append(str(p))
    return {"line_files": files, "total_chars": 100, "chars_synthesized": 100, "section_markers": markers, "generated": len(files), "reused": 0, "failed": 0}


def test_full_run_publishes_then_is_idempotent(settings, monkeypatch):
    monkeypatch.setattr(workflow, "ingest_main", lambda s: STORIES)
    monkeypatch.setattr(workflow, "ingest_aisle", lambda s: [])
    monkeypatch.setattr(workflow, "generate_tts", _fake_tts)

    result = workflow.run_episode(settings, "2026-09-26")
    assert result["status"] == "PUBLISHED", result
    assert result["audio_url"].endswith("/episodes/2026-09-26/episode_2026-09-26.mp3")
    assert set(result["feeds"]) == {"standard", "extended"}

    site = settings.local_site_dir
    assert (site / "feed.xml").exists() and (site / "cover.png").exists()
    assert (site / "episodes/2026-09-26/episode.json").exists()
    assert (site / "state/memory.json").exists() and (site / "state/costs.json").exists()
    assert (site / "work/2026-09-26/checkpoints/writer.json").exists()
    assert "PUBLISHED" in (settings.output_dir / "2026-09-26/status.json").read_text()

    again = workflow.run_episode(settings, "2026-09-26")
    assert again["status"] == "ALREADY_PUBLISHED"


def test_dry_run_stops_before_audio(settings, monkeypatch):
    monkeypatch.setattr(workflow, "ingest_main", lambda s: STORIES)
    monkeypatch.setattr(workflow, "ingest_aisle", lambda s: [])
    settings.dry_run = True
    result = workflow.run_episode(settings, "2026-09-27")
    assert result["status"] == "DRY_RUN"
    assert (settings.output_dir / "2026-09-27/script.txt").exists()
    assert not (settings.local_site_dir / "feed.xml").exists()
