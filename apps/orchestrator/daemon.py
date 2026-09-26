"""Daemon loop — autonomous production cycle.

Cycle:
  1. Resume any in-flight projects (produce all)
  2. Create a new project if fewer than N are awaiting approval
  3. Sleep, repeat.

Run:  avf daemon --interval 900 --target 5
Stop: Ctrl+C (state machine keeps everything resumable).
"""
from __future__ import annotations

import time

from core.logging import get_logger

log = get_logger("daemon")


def run_daemon(interval: int = 900, target: int = 5,
               max_cycles: int | None = None,
               videos_per_day: int = 3,
               start_hour: int = 1, end_hour: int = 7,
               stop=None, retired=None) -> None:
    """Keep the factory running. target = videos awaiting approval.

    videos_per_day/start_hour/end_hour throttle new production to a window
    (default 01:00-07:00, 3 videos) per spec §39.
    stop/retired: pollables — when either turns True the loop exits
    (used by the API server thread; CLI just runs until Ctrl+C).
    """
    from datetime import datetime

    from apps.analytics.collector import collect_for_project
    from apps.analytics.feedback import rescore_pending_ideas
    from apps.orchestrator.pipeline import create_project, get_db, run_pipeline

    def _done() -> bool:
        return bool((stop and stop()) or (retired and retired()))

    db = get_db()
    cycle = 0
    while not _done() and (max_cycles is None or cycle < max_cycles):
        cycle += 1
        try:
            # 0. analytics sync + feedback loop every cycle (cheap API calls)
            for p in db.all("projects"):
                if p.get("youtube_id"):
                    try:
                        collect_for_project(db, p["id"])
                    except Exception:
                        log.warning("analytics sync failed for %s", p["id"])
            rescore_pending_ideas(db)

            # 1. resume in-flight (skip FAILED and human-gated APPROVAL)
            for p in db.incomplete_projects():
                st = p["status"]
                if st in ("APPROVAL", "FAILED", "COMPLETE"):
                    continue
                log.info("resume project %s @ %s", p["id"], st)
                try:
                    run_pipeline(p["id"])
                except Exception as e:  # one bad project must not kill the cycle
                    log.exception("project %s failed: %s", p["id"], e)

            # 2. top up pipeline — only inside the production window
            hour = datetime.now().hour
            in_window = start_hour <= hour < end_hour or start_hour == end_hour
            made_today = len(db.all(
                "projects",
                f"created_at >= date('now') AND status != 'FAILED'"))
            awaiting = len(db.all("projects", "status=?", ("APPROVAL",)))
            in_flight = len(db.all(
                "projects",
                "status NOT IN ('APPROVAL','FAILED','COMPLETE','IDEA')"))
            if in_window and made_today < videos_per_day \
                    and awaiting + in_flight < target:
                pid = create_project()
                log.info("new project %s", pid)
                try:
                    run_pipeline(pid)
                except Exception as e:  # one bad project must not kill the cycle
                    log.exception("project %s failed: %s", pid, e)
            elif not in_window:
                log.info("outside production window (%02d:00-%02d:00), idling",
                         start_hour, end_hour)

            stats = {st: len(db.all("projects", "status=?", (st,)))
                     for st in ("APPROVAL", "FAILED", "COMPLETE")}
            log.info("cycle %d done: %s", cycle, stats)
        except Exception:  # daemon must never die
            log.exception("cycle %d crashed, continuing", cycle)
        for _ in range(0, interval, 5):  # wake often so stop reacts fast
            if _done():
                log.info("daemon stopped after cycle %d", cycle)
                return
            time.sleep(min(5, interval))
