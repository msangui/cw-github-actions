"""Command line entrypoint: `python -m pipeline <command>` (or `cw <command>` once installed)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from pipeline.config import get_settings
from pipeline.log import get_logger

log = get_logger(component="cli")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def cmd_run(args: argparse.Namespace) -> int:
    from pipeline.workflow import run_episode

    s = get_settings()
    s.dry_run = args.dry_run
    s.skip_publish = args.no_publish
    s.skip_aisle = args.skip_aisle
    s.fresh = args.fresh
    episode_date = args.date or _today()
    date.fromisoformat(episode_date)  # validate

    result = run_episode(s, episode_date)
    print(json.dumps(result, indent=2, default=str))
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"status={result.get('status')}\n")
            f.write(f"episode_date={episode_date}\n")
            for k in ("audio_url", "extended_audio_url", "newsletter_url"):
                if result.get(k):
                    f.write(f"{k}={result[k]}\n")
            feeds = result.get("feeds") or {}
            if feeds.get("standard"):
                f.write(f"feed_url={feeds['standard']}\n")
    return 0 if result.get("status") in {"PUBLISHED", "SAFE_MODE", "DRY_RUN", "AUDIO_READY", "ALREADY_PUBLISHED"} else 1


def cmd_rebuild_feed(_: argparse.Namespace) -> int:
    from pipeline.feed import rebuild_feeds
    from pipeline.stages.publish import ensure_cover
    from pipeline.storage import Storage

    s = get_settings()
    storage = Storage(s)
    ensure_cover(s, storage)
    urls = rebuild_feeds(s, storage)
    print(json.dumps(urls, indent=2))
    return 0


def cmd_check_config(_: argparse.Namespace) -> int:
    from pipeline.stages.stitch import ffmpeg_available

    s = get_settings()
    checks = {
        "anthropic_api_key": bool(s.anthropic_api_key),
        "elevenlabs_api_key": bool(s.elevenlabs_api_key),
        "openai_api_key (optional)": bool(s.openai_api_key),
        "serper_api_key (optional)": bool(s.serper_api_key),
        "telegram (optional)": bool(s.telegram_bot_token and s.telegram_chat_id),
        "storage": f"s3://{s.s3_bucket}/{s.s3_prefix}" if s.uses_s3 else f"local:{s.local_site_dir}",
        "public_base_url": s.resolved_public_base_url,
        "intro_mp3": s.intro_mp3_path.exists(),
        "cover": s.cover_path.exists(),
        "ffmpeg": ffmpeg_available(),
        "claire_voice": bool(s.voice("CLAIRE").get("voice_id")),
        "flint_voice": bool(s.voice("FLINT").get("voice_id")),
        "agents": sorted(p.stem for p in (s.config_dir / "agents").glob("*.yaml")),
        "sources_main": len(s.load_yaml("sources").get("main", [])),
        "sources_aisle": len(s.load_yaml("sources").get("aisle", {}).get("sources", [])),
    }
    print(json.dumps(checks, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cw", description="Context Window podcast pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Produce (and publish) an episode")
    p_run.add_argument("--date", help="Episode date YYYY-MM-DD (default: today UTC)")
    p_run.add_argument("--dry-run", action="store_true", help="Text pipeline only: no TTS, no publish")
    p_run.add_argument("--no-publish", action="store_true", help="Generate audio but do not upload or touch the feed")
    p_run.add_argument("--skip-aisle", action="store_true", help="Skip The Aisle segment")
    p_run.add_argument("--fresh", action="store_true", help="Ignore checkpoints and regenerate everything")
    p_run.add_argument("--github-output", help="Path of $GITHUB_OUTPUT to append step outputs to")
    p_run.set_defaults(func=cmd_run)

    p_feed = sub.add_parser("rebuild-feed", help="Rebuild feed.xml from the episodes in storage")
    p_feed.set_defaults(func=cmd_rebuild_feed)

    p_check = sub.add_parser("check-config", help="Print which keys/tools are configured")
    p_check.set_defaults(func=cmd_check_config)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
