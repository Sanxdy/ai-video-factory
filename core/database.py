"""SQLite database layer — all tables from the spec, thread-safe connections."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    language TEXT DEFAULT 'en',
    niche TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER,
    topic TEXT NOT NULL,
    source TEXT,
    score REAL DEFAULT 0,
    status TEXT DEFAULT 'NEW',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL,
    summary TEXT,
    facts TEXT,
    sources TEXT,
    confidence REAL DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL,
    version INTEGER DEFAULT 1,
    hook TEXT,
    body TEXT,
    cta TEXT,
    duration REAL,
    status TEXT DEFAULT 'DRAFT',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER NOT NULL,
    scene_number INTEGER,
    duration REAL,
    narration TEXT,
    visual_prompt TEXT,
    stock_query TEXT DEFAULT '',
    motion_prompt TEXT,
    text_overlay TEXT,
    asset_type TEXT DEFAULT 'image',
    asset_path TEXT,
    pause_after REAL DEFAULT 0,
    status TEXT DEFAULT 'PENDING'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenes_unique ON scenes(script_id, scene_number);
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id INTEGER,
    type TEXT,
    path TEXT,
    model TEXT,
    generation_time REAL,
    status TEXT DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    entity_id INTEGER,
    status TEXT DEFAULT 'PENDING',
    attempts INTEGER DEFAULT 0,
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER NOT NULL,
    project_path TEXT,
    rendered_path TEXT,
    duration REAL,
    resolution TEXT,
    quality_score REAL,
    status TEXT DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    platform TEXT DEFAULT 'youtube',
    external_id TEXT,
    url TEXT,
    status TEXT DEFAULT 'PENDING',
    uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL DEFAULT 0,
    youtube_id TEXT DEFAULT '',
    title TEXT DEFAULT '',
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    watch_time REAL DEFAULT 0,
    retention REAL DEFAULT 0,
    collected_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER NOT NULL,
    manifest_path TEXT,
    status TEXT DEFAULT 'CREATED',
    created_at TEXT,
    idea_id INTEGER,
    youtube_id TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA)
            # lightweight migrations: existing DBs miss columns added later
            cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
            if "archived" not in cols:
                conn.execute("ALTER TABLE projects ADD COLUMN archived INTEGER DEFAULT 0")
            analytics_cols = {r[1] for r in conn.execute("PRAGMA table_info(analytics)")}
            if "title" not in analytics_cols:
                conn.execute("ALTER TABLE analytics ADD COLUMN title TEXT DEFAULT ''")
            if "youtube_id" not in analytics_cols:
                conn.execute("ALTER TABLE analytics ADD COLUMN youtube_id TEXT DEFAULT ''")
            # storyboard-only columns
            if "aspect_ratio" not in cols:
                conn.execute("ALTER TABLE projects ADD COLUMN aspect_ratio TEXT DEFAULT '9:16'")
            if "storyboard_only" not in cols:
                conn.execute("ALTER TABLE projects ADD COLUMN storyboard_only INTEGER DEFAULT 0")
            scene_cols = {r[1] for r in conn.execute("PRAGMA table_info(scenes)")}
            if "pause_after" not in scene_cols:
                conn.execute("ALTER TABLE scenes ADD COLUMN pause_after REAL DEFAULT 0")
            conn.commit()

    # ── generic helpers ────────────────────────────────
    def insert(self, table: str, **fields) -> int:
        cols = list(fields)
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [fields[c] for c in cols],
            )
            conn.commit()
            return cur.lastrowid

    def update(self, table: str, row_id: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), row_id])
            conn.commit()

    def get(self, table: str, row_id: int) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
            return dict(row) if row else None

    def delete(self, table: str, row_id: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
            conn.commit()

    def all(self, table: str, where: str = "", params: tuple = ()) -> list[dict]:
        with self._lock, self._conn() as conn:
            q = f"SELECT * FROM {table}"
            if where:
                if "ORDER BY" in where.upper():
                    q += f" WHERE {where}"
                else:
                    q += f" WHERE {where} ORDER BY id DESC"
            else:
                q += " ORDER BY id DESC"
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]

    # ── settings (key/value) ───────────────────────────
    def get_setting(self, key: str) -> str | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()

    # ── jobs ───────────────────────────────────────────
    def create_job(self, job_type: str, entity_id: int | None = None) -> int:
        return self.insert("jobs", job_type=job_type, entity_id=entity_id,
                           status="PENDING", attempts=0)

    def job_status(self, job_id: int) -> dict | None:
        return self.get("jobs", job_id)

    def set_job(self, job_id: int, status: str, error: str | None = None) -> None:
        f = {"status": status}
        if status in ("RUNNING", "RETRYING"):
            f["started_at"] = _now()
        if status in ("SUCCESS", "FAILED", "CANCELLED"):
            f["finished_at"] = _now()
        if error is not None:
            f["error"] = error[:500]
        if status == "RETRYING":
            f["attempts"] = (self.get("jobs", job_id) or {}).get("attempts", 0) + 1
        self.update("jobs", job_id, **f)

    def pending_jobs(self, job_type: str | None = None) -> list[dict]:
        where = "status IN ('PENDING','RETRYING')"
        params: tuple = ()
        if job_type:
            where += " AND job_type=?"
            params = (job_type,)
        return self.all("jobs", where, params)

    def incomplete_projects(self) -> list[dict]:
        """Projects whose state machine isn't terminal — for resume."""
        return self.all("projects", "status NOT IN ('COMPLETE','FAILED','CANCELLED')")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()