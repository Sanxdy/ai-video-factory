"""Application configuration — loads from config/app.yaml + optional .env overrides."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def platform_data_dir() -> Path:
    """Where a packaged app keeps its state, by platform convention."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AVF"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "AVF"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "avf"


def data_dir() -> Path:
    """Where mutable state lives (content/, runtime/, database/).

    A packaged app bundle is read-only (macOS .app, Windows Program Files), so the
    launcher sets AVF_DATA_DIR. A source checkout keeps its data in the repo, which
    is what every existing install and the test suite expect.
    """
    env = os.environ.get("AVF_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return ROOT if os.access(ROOT, os.W_OK) else platform_data_dir()


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.env = _load_yaml(root / "config" / "app.yaml")
        self.models_cfg = _load_yaml(root / "config" / "models.yaml")
        self.channels_cfg = _load_yaml(root / "config" / "channels.yaml")
        self.content_rules = _load_yaml(root / "config" / "content-rules.yaml")

        # Env overrides (secrets / machine-specific)
        env_override = {}
        for key, val in os.environ.items():
            if key.startswith("AVF_"):
                env_override[key[4:].lower()] = val
        self.env = _deep_merge(self.env, {"env": env_override})

    @property
    def project_name(self) -> str:
        return self.env.get("project", {}).get("name", "ai-video-factory")

    @property
    def paths(self):
        return self.env.get("paths", {})

    @property
    def db_path(self) -> Path:
        """The one SQLite file. Resolved per call so AVF_DATA_DIR can move it."""
        return data_dir() / "database" / "factory.db"

    def path(self, key: str) -> Path:
        p = self.paths.get(key, key)
        return self.root / p

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.env
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur


config = Config()