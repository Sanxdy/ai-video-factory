"""Persisted key/value settings (settings table in factory.db).

Keys used today: llm.api_base_url, llm.api_key, llm.api_model,
llm.api_fallback_models.
"""
from __future__ import annotations

from core.config import config
from core.database import Database


def get_db() -> Database:
    return Database(config.db_path)


def get_setting(key: str, default: str | None = None) -> str | None:
    v = get_db().get_setting(key)
    return default if v is None else v


def set_setting(key: str, value: str) -> None:
    get_db().set_setting(key, value)
