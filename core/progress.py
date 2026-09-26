"""Live pipeline progress — in-memory per-project progress for the UI.

progress.set() emits the existing "project_state" bus event so SSE pushes
updates to connected clients with no extra wiring.
"""
from __future__ import annotations

import time

from core import events

# pid → {"stage": str, "pct": int, "detail": str, "started": epoch}
_P: dict[int, dict] = {}


def set_progress(pid: int, stage: str, pct: int, detail: str = "") -> None:
    first = pid not in _P
    _P[pid] = {"stage": stage, "pct": max(0, min(100, int(pct))),
               "detail": detail, "started": _P[pid]["started"] if not first else time.time()}
    events.emit("project_state", project_id=pid)


def get_progress(pid: int) -> dict | None:
    return _P.get(pid)


def clear_progress(pid: int) -> None:
    _P.pop(pid, None)
