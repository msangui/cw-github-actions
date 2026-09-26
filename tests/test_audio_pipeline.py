"""ffmpeg-backed tests: synthetic tone MP3s stand in for ElevenLabs lines."""

import subprocess
from pathlib import Path

import pytest

from pipeline.feed import load_episodes
from pipeline.memory import HeadlineMemory
from pipeline.stages.publish import publish_episode
from pipeline.stages.stitch import ffmpeg_available, probe_duration, stitch, stitch_extended
from pipeline.storage import Storage

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _tone(path: Path, seconds: float, freq: int) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-q:a", "4", str(path)],
        check=True,
    )
    return str(path)


def test_stitch_with_intro_and_extended(settings, tmp_path):
    lines = [_tone(tmp_path / f"line_{i:04d}.mp3", 3.0, 300 + 100 * i) for i in range(4)]
    aisle = [_tone(tmp_path / f"aisle_{i:04d}.mp3", 3.0, 900) for i in range(2)]

    final = stitch(settings, lines, "2026-09-26", tmp_path / "ep")
    assert Path(final).name == "episode_2026-09-26.mp3"
    dur = probe_duration(final)
    # 12s TTS delay + 12s of lines = 24s, longer than the ~19s intro (amix duration=longest)
    assert 23.0 <= dur <= 26.0

    ext = stitch_extended(settings, lines, aisle, 2, "2026-09-26", tmp_path / "ep")
    assert Path(ext).name == "episode_2026-09-26_aisle.mp3"
    assert probe_duration(ext) >= dur + 5.0  # two extra 3s aisle lines

    voice_only = stitch(settings, lines, "2026-09-26", tmp_path / "ep", suffix="_solo", skip_intro=True)
    assert 11.5 <= probe_duration(voice_only) <= 13.0


def test_publish_writes_episode_json_memory_and_feed(settings, tmp_path):
    storage = Storage(settings)
    memory = HeadlineMemory(storage)
    mp3 = Path(_tone(tmp_path / "episode_2026-09-26.mp3", 2.0, 440))
    stories = [{"title": "Big model drop", "url": "https://x/1", "source_name": "Src"}]
    result = publish_episode(
        settings, storage, memory, "2026-09-26", "PUBLISHED",
        {"title": "Ep", "description": "d", "story_count": 1, "deep_dive_picks": [{"title": "Big model drop", "url": "https://x/1", "tease": ""}]},
        "FLINT: hi\nCLAIRE: bye", stories, [], str(mp3), None, "<html>n</html>", [], {"total_usd": 1.0},
    )
    assert result["audio_url"].endswith("episodes/2026-09-26/episode_2026-09-26.mp3")
    eps = load_episodes(storage)
    assert len(eps) == 1 and eps[0]["audio"]["standard"]["bytes"] == mp3.stat().st_size
    assert 1.5 <= eps[0]["audio"]["standard"]["duration_seconds"] <= 2.5
    assert storage.exists("feed.xml") and storage.exists("cover.png") and storage.exists("episodes/2026-09-26/newsletter.html")
    fresh = HeadlineMemory(storage)
    assert fresh.was_covered({"title": "Big model drop", "url": ""}, "headlines", 21, 0.6)
    # Same-day rerun of publish is idempotent
    publish_episode(settings, storage, memory, "2026-09-26", "PUBLISHED", {"title": "Ep"}, "FLINT: hi", stories, [], str(mp3), None, None, [])
    assert len(load_episodes(storage)) == 1
