"""Settings (from environment) and YAML config loaders.

Replaces the pydantic-settings + Postgres agent_configs/tool_configs of the original.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # LLM
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))

    # TTS
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    claire_voice_id: str = field(default_factory=lambda: _env("CLAIRE_VOICE_ID"))
    flint_voice_id: str = field(default_factory=lambda: _env("FLINT_VOICE_ID"))

    # Search
    serper_api_key: str = field(default_factory=lambda: _env("SERPER_API_KEY"))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # Storage
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1")))
    s3_prefix: str = field(default_factory=lambda: _env("S3_PREFIX").strip("/"))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL").rstrip("/"))

    # Paths
    config_dir: Path = field(default_factory=lambda: Path(_env("CW_CONFIG_DIR", str(REPO_ROOT / "config"))))
    output_dir: Path = field(default_factory=lambda: Path(_env("CW_OUTPUT_DIR", str(REPO_ROOT / "output" / "episodes"))))
    local_site_dir: Path = field(default_factory=lambda: Path(_env("CW_SITE_DIR", str(REPO_ROOT / "output" / "site"))))
    intro_mp3_path: Path = field(default_factory=lambda: Path(_env("INTRO_MP3_PATH", str(REPO_ROOT / "assets" / "intro.mp3"))))
    cover_path: Path = field(default_factory=lambda: Path(_env("COVER_PATH", str(REPO_ROOT / "assets" / "cover.png"))))

    # Safety valves
    ignore_budget: bool = field(default_factory=lambda: _env_bool("CW_IGNORE_BUDGET", False))

    # Run flags (set by CLI)
    dry_run: bool = False          # no TTS, no publish
    skip_publish: bool = False     # produce audio but do not upload / touch feeds
    skip_aisle: bool = False
    fresh: bool = False            # ignore checkpoints

    # ── derived ─────────────────────────────────────────────────────────────
    @property
    def uses_s3(self) -> bool:
        return bool(self.s3_bucket)

    @property
    def resolved_public_base_url(self) -> str:
        if self.public_base_url:
            base = self.public_base_url
        elif self.s3_bucket:
            base = f"https://{self.s3_bucket}.s3.{self.aws_region}.amazonaws.com"
        else:
            base = f"file://{self.local_site_dir.resolve()}"
        if self.s3_prefix:
            base = f"{base}/{self.s3_prefix}"
        return base

    # ── YAML config access ──────────────────────────────────────────────────
    def load_yaml(self, name: str) -> dict[str, Any]:
        return _load_yaml_cached(str(self.config_dir / f"{name}.yaml"))

    def agent(self, key: str) -> dict[str, Any]:
        """Config for one agent (config/agents/<key>.yaml)."""
        return _load_yaml_cached(str(self.config_dir / "agents" / f"{key}.yaml"))

    def voice(self, speaker: str) -> dict[str, Any]:
        voices = self.load_yaml("voices")
        v = dict(voices.get("voices", {}).get(speaker, {}))
        override = self.claire_voice_id if speaker == "CLAIRE" else self.flint_voice_id
        if override:
            v["voice_id"] = override
        return v


@lru_cache(maxsize=64)
def _load_yaml_cached(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def get_settings() -> Settings:
    return Settings()
