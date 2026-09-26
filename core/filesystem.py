"""Filesystem helpers — content dir structure (spec §3) + cache (spec §30)."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from core.config import data_dir


def ensure_dirs() -> None:
    """Create the full content tree if missing."""
    root = data_dir()
    for sub in ("ideas", "research", "scripts", "storyboards", "assets",
                "audio", "subtitles", "projects", "rendered", "approved", "uploaded"):
        (root / "content" / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("logs", "jobs", "cache", "locks"):
        (root / "runtime" / sub).mkdir(parents=True, exist_ok=True)


def project_dir(project_id: int) -> Path:
    p = data_dir() / "content" / "projects" / f"project-{project_id:05d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def cache_key(*parts: str) -> str:
    """SHA-256 cache key: hash(prompt + model + seed + params) (spec §30)."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class AssetCache:
    """Content-addressed cache keyed by SHA-256 of generation inputs."""

    def __init__(self, cache_dir: Path | None = None):
        self.dir = cache_dir or (data_dir() / "runtime" / "cache")
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, suffix: str = ".bin") -> Path | None:
        p = self.dir / f"{key}{suffix}"
        return p if p.exists() else None

    def put(self, key: str, data: bytes, suffix: str = ".bin") -> Path:
        p = self.dir / f"{key}{suffix}"
        p.write_bytes(data)
        return p

    def clear(self) -> int:
        n = len(list(self.dir.iterdir()))
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        return n