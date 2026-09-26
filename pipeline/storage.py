"""Object storage abstraction: S3 in production, a local directory for dev/tests.

Keys are bucket-relative (before the optional S3_PREFIX). Everything written here is
what the podcast feed and Spotify will fetch, so content types matter.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Optional

from pipeline.config import Settings
from pipeline.log import get_logger

log = get_logger(component="storage")

CACHE_LONG = "public, max-age=31536000, immutable"
CACHE_SHORT = "public, max-age=300"

_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".xml": "application/rss+xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def guess_content_type(key: str) -> str:
    suffix = Path(key).suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.prefix = settings.s3_prefix
        self.public_base = settings.resolved_public_base_url
        if settings.uses_s3:
            import boto3

            self._s3 = boto3.client("s3", region_name=settings.aws_region)
            self.bucket = settings.s3_bucket
            self.local_root: Optional[Path] = None
            log.info("Using S3 storage", bucket=self.bucket, prefix=self.prefix or "(none)")
        else:
            self._s3 = None
            self.bucket = ""
            self.local_root = settings.local_site_dir
            self.local_root.mkdir(parents=True, exist_ok=True)
            log.info("Using local storage", root=str(self.local_root))

    # ── key helpers ────────────────────────────────────────────────────────
    def _full_key(self, key: str) -> str:
        key = key.lstrip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    def _local_path(self, key: str) -> Path:
        assert self.local_root is not None
        return self.local_root / key.lstrip("/")

    def public_url(self, key: str) -> str:
        return f"{self.public_base}/{key.lstrip('/')}"

    # ── writes ─────────────────────────────────────────────────────────────
    def put_file(self, key: str, path: Path | str, content_type: str | None = None, cache_control: str | None = None) -> str:
        path = Path(path)
        ct = content_type or guess_content_type(key)
        if self._s3:
            extra: dict[str, Any] = {"ContentType": ct}
            if cache_control:
                extra["CacheControl"] = cache_control
            self._s3.upload_file(str(path), self.bucket, self._full_key(key), ExtraArgs=extra)
        else:
            dest = self._local_path(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        log.debug("put_file", key=key, bytes=path.stat().st_size, content_type=ct)
        return self.public_url(key)

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None, cache_control: str | None = None) -> str:
        ct = content_type or guess_content_type(key)
        if self._s3:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": self._full_key(key), "Body": data, "ContentType": ct}
            if cache_control:
                kwargs["CacheControl"] = cache_control
            self._s3.put_object(**kwargs)
        else:
            dest = self._local_path(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        log.debug("put_bytes", key=key, bytes=len(data), content_type=ct)
        return self.public_url(key)

    def put_json(self, key: str, obj: Any, cache_control: str | None = CACHE_SHORT) -> str:
        data = json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        return self.put_bytes(key, data, "application/json; charset=utf-8", cache_control)

    # ── reads ──────────────────────────────────────────────────────────────
    def get_bytes(self, key: str) -> Optional[bytes]:
        if self._s3:
            try:
                resp = self._s3.get_object(Bucket=self.bucket, Key=self._full_key(key))
                return resp["Body"].read()
            except self._s3.exceptions.NoSuchKey:
                return None
            except Exception as e:  # botocore ClientError 404 etc.
                if "NoSuchKey" in str(e) or "Not Found" in str(e) or "404" in str(e):
                    return None
                raise
        p = self._local_path(key)
        return p.read_bytes() if p.exists() else None

    def get_json(self, key: str) -> Optional[Any]:
        raw = self.get_bytes(key)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            log.warning("Corrupt JSON object in storage", key=key)
            return None

    def download(self, key: str, dest: Path | str) -> bool:
        data = self.get_bytes(key)
        if data is None:
            return False
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True

    def exists(self, key: str) -> bool:
        if self._s3:
            try:
                self._s3.head_object(Bucket=self.bucket, Key=self._full_key(key))
                return True
            except Exception:
                return False
        return self._local_path(key).exists()

    def size(self, key: str) -> int:
        if self._s3:
            resp = self._s3.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return int(resp["ContentLength"])
        return self._local_path(key).stat().st_size

    def list_keys(self, prefix: str) -> list[str]:
        """List bucket-relative keys under prefix (recursive)."""
        prefix = prefix.lstrip("/")
        if self._s3:
            full_prefix = self._full_key(prefix)
            keys: list[str] = []
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    if self.prefix and k.startswith(self.prefix + "/"):
                        k = k[len(self.prefix) + 1 :]
                    keys.append(k)
            return sorted(keys)
        root = self._local_path(prefix)
        if not root.exists():
            return []
        assert self.local_root is not None
        return sorted(str(p.relative_to(self.local_root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())

    def delete(self, key: str) -> None:
        if self._s3:
            self._s3.delete_object(Bucket=self.bucket, Key=self._full_key(key))
        else:
            p = self._local_path(key)
            if p.exists():
                p.unlink()
