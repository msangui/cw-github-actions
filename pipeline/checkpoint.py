"""Stage checkpoints: the poor man's Temporal replay.

Each completed stage writes its output to output/episodes/<date>/checkpoints/<stage>.json
and mirrors it to <storage>/work/<date>/checkpoints/<stage>.json. A re-run of the same
date (manual retry, GitHub re-run) loads completed stages instead of paying for them
again. `--fresh` ignores existing checkpoints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pipeline.log import get_logger
from pipeline.storage import Storage

log = get_logger(component="checkpoint")


class Checkpoints:
    def __init__(self, storage: Storage, episode_date: str, local_dir: Path, fresh: bool = False):
        self.storage = storage
        self.episode_date = episode_date
        self.local_dir = local_dir / "checkpoints"
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.fresh = fresh

    def _key(self, stage: str) -> str:
        return f"work/{self.episode_date}/checkpoints/{stage}.json"

    def load(self, stage: str) -> Optional[Any]:
        if self.fresh:
            return None
        local = self.local_dir / f"{stage}.json"
        if local.exists():
            try:
                data = json.loads(local.read_text(encoding="utf-8"))
                log.info("Loaded checkpoint (local)", stage=stage)
                return data
            except json.JSONDecodeError:
                pass
        data = self.storage.get_json(self._key(stage))
        if data is not None:
            local.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            log.info("Loaded checkpoint (remote)", stage=stage)
        return data

    def save(self, stage: str, data: Any) -> None:
        local = self.local_dir / f"{stage}.json"
        local.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        try:
            self.storage.put_json(self._key(stage), data, cache_control="private, no-store")
        except Exception as e:
            log.warning("Could not mirror checkpoint to storage", stage=stage, error=str(e))
        log.debug("Saved checkpoint", stage=stage)

    def clear(self) -> None:
        for k in self.storage.list_keys(f"work/{self.episode_date}/"):
            self.storage.delete(k)
