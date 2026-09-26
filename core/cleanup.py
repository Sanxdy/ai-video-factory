"""Retention cleanup module — purge old project files & DB entries to free disk space."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import shutil
from typing import List, Dict, Any

from core.config import data_dir
from core.database import Database
from core.logging import get_logger

log = get_logger("cleanup")


def get_dir_size(path: str | os.PathLike) -> int:
    """Get total size of directory in bytes."""
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
    except Exception:
        pass
    return total


def free_project_files(pid: int, render_only: bool = False) -> int:
    """Delete project media from disk, keep DB rows. Returns bytes freed.

    render_only=True keeps content/projects/ so a re-render is still possible
    (gallery "delete video" = drop the finished file, keep the project).
    """
    dirs = [data_dir() / "content" / "rendered" / f"project-{pid:05d}"]
    if not render_only:
        dirs.append(data_dir() / "content" / "projects" / f"project-{pid:05d}")

    bytes_freed = sum(get_dir_size(d) for d in dirs)
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    log.info(f"Freed media for project {pid} ({bytes_freed / (1024 * 1024):.1f} MB)")
    return bytes_freed


def purge_project(db: Database, pid: int) -> int:
    """Permanently delete project PID: cascaded DB rows + files on disk. Returns bytes freed."""
    p = db.get("projects", pid)
    if not p:
        return 0

    bytes_freed = free_project_files(pid)

    sid, iid = p.get("script_id"), p.get("idea_id")
    if sid:
        # ponytail: skip analytics+uploads cascade — YouTube stats persist after gallery delete
        for vid in [v["id"] for v in db.all("videos", "script_id=?", (sid,))]:
            db.delete("videos", vid)
        for sc in [s["id"] for s in db.all("scenes", "script_id=?", (sid,))]:
            for a in db.all("assets", "scene_id=?", (sc,)):
                db.delete("assets", a["id"])
            db.delete("scenes", sc)
        db.delete("scripts", sid)
    if iid:
        for r in db.all("research", "idea_id=?", (iid,)):
            db.delete("research", r["id"])
        db.delete("ideas", iid)

    db.delete("projects", pid)

    log.info(f"Purged project {pid} ({bytes_freed / (1024 * 1024):.1f} MB freed)")
    return bytes_freed


def run_retention_cleanup(db: Database, days: int = 7) -> Dict[str, Any]:
    """Find and purge completed projects created more than `days` ago."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    projects = db.all("projects")
    to_delete: List[int] = []

    for p in projects:
        # Only cleanup finished or failed projects, never in-progress ones
        if p.get("status") not in ("COMPLETE", "PUBLISHED", "FAILED"):
            continue

        created_str = p.get("created_at")
        if not created_str:
            continue

        try:
            created_at = datetime.fromisoformat(created_str)
            if created_at < cutoff:
                to_delete.append(p["id"])
        except Exception:
            pass

    freed_bytes = 0
    deleted_count = 0
    trimmed_count = 0

    for pid in to_delete:
        p = db.get("projects", pid)
        # published work keeps its dashboard record (and its analytics);
        # retention only reclaims the local media
        if p and p.get("youtube_id"):
            freed_bytes += free_project_files(pid)
            trimmed_count += 1
            continue
        freed_bytes += purge_project(db, pid)
        deleted_count += 1

    return {
        "deleted_count": deleted_count,
        "trimmed_count": trimmed_count,
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "days": days,
    }
