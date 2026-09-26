"""Daily upload schedule — Mon–Sun themes mapped to YouTube playlists.

Settings key `upload.schedules` (JSON array):
  [{"id": "abc", "enabled": true, "time": "19:00", "videos_per_run": 2,
    "privacy": "public",
    "days": {"mon": {"topic": "Cat comedy", "playlist": "Cat Funny Videos"}, ...},
    ...}]

Backward compat: `upload.schedule` (v1 single schedule) auto-migrated.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

from core.config import data_dir
from core.settings import get_setting, set_setting

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# ponytail: schedules are authored in WIB (GMT+7) and cron converts to UTC;
# weekday lookup must use the same zone or the 06:00 run reads yesterday's theme.
TZ_OFFSET_HOURS = 7


def local_now():
    """Schedule-local (WIB) wall clock. All day/week lookups go through this."""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=TZ_OFFSET_HOURS)))


def _empty_days() -> dict:
    return {d: {"topic": "", "playlist": "", "music": ""} for d in DAYS}


# A landscape run renders ~6x realtime (~20-35 min per 5-6 min video on this
# 2-core box), so it cannot be batched like a Short. Shorts keep the old 20 cap.
RATIOS = ("9:16", "16:9")
MAX_VIDEOS_PER_RUN = {"9:16": 20, "16:9": 2}
DEFAULT_TARGET_MINUTES = 6.0


def default_schedule() -> dict:
    return {"id": str(uuid.uuid4()), "enabled": False, "time": "09:00",
            "videos_per_run": 1, "privacy": "public", "format": "9:16",
            "target_minutes": DEFAULT_TARGET_MINUTES, "days": _empty_days()}


SCHEDULE_KEYS = ("id", "enabled", "time", "videos_per_run", "privacy",
                 "format", "target_minutes")


def _has_topics(sched: dict) -> bool:
    return any(
        (sched.get("days") or {}).get(d, {}).get("topic", "").strip()
        for d in DAYS
    )


def _is_useful(sched: dict) -> bool:
    return bool(sched.get("enabled")) and _has_topics(sched)


def _legacy_schedule() -> dict | None:
    """Return a usable legacy v1 schedule, or None."""
    raw = get_setting("upload.schedule")
    if not raw:
        return None
    try:
        old = json.loads(raw)
        if isinstance(old, dict) and _is_useful(old):
            return _clean_schedule(old)
    except (TypeError, ValueError):
        pass
    return None


def _stored_schedules() -> list[dict] | None:
    """The schedules exactly as stored, or None if the key was never written.

    None means "migrate me" — a v1 install only has `upload.schedule`. An
    empty list means "the user removed them all" and must stay empty.
    """
    raw = get_setting("upload.schedules")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [_clean_schedule(s) for s in data if isinstance(s, dict)]


def _clean_schedule(sched: dict) -> dict:
    out = default_schedule()
    out["id"] = sched.get("id") or str(uuid.uuid4())
    out["enabled"] = bool(sched.get("enabled"))
    out["time"] = str(sched.get("time") or "09:00")
    # missing format → "9:16": schedules saved before long-form existed keep
    # producing exactly what they produced before.
    fmt = str(sched.get("format") or "")
    if fmt not in RATIOS:
        fmt = "9:16"
    out["format"] = fmt
    cap = MAX_VIDEOS_PER_RUN[fmt]
    out["videos_per_run"] = max(1, min(cap, int(sched.get("videos_per_run") or 1)))
    try:
        mins = float(sched.get("target_minutes") or DEFAULT_TARGET_MINUTES)
    except (TypeError, ValueError):
        mins = DEFAULT_TARGET_MINUTES
    out["target_minutes"] = max(5.0, min(20.0, mins))
    out["privacy"] = sched.get("privacy") if sched.get("privacy") in (
        "public", "unlisted", "private") else "public"
    days = sched.get("days") or {}
    for d in DAYS:
        e = days.get(d) or {}
        out["days"][d] = {"topic": str(e.get("topic") or "").strip(),
                          "playlist": str(e.get("playlist") or "").strip(),
                          "music": str(e.get("music") or "").strip()}
    return out


# ── Multi-schedule (flexible) API ─────────────────────────

def get_schedules() -> list[dict]:
    """All configured schedules, migrated from legacy v1 if needed.

    The setting is the single source of truth: a schedule the user disabled
    stays disabled, and an empty list stays empty. Only a never-written key
    migrates from the v1 `upload.schedule` (see `_stored_schedules`), so
    switching every schedule off actually stops the cron entries instead of
    silently resurrecting the old one.
    """
    stored = _stored_schedules()
    if stored is None:                      # key absent → one-time v1 migration
        legacy = _legacy_schedule()
        return [legacy] if legacy else [default_schedule()]
    if not stored:                          # explicitly emptied
        return [default_schedule()]
    return stored


def save_schedules(scheds: list[dict]) -> list[dict]:
    cleaned = [_clean_schedule(s) for s in scheds]
    set_setting("upload.schedules", json.dumps(cleaned))
    sync_cron()
    return cleaned


def add_schedule(scheds: list[dict], entry: dict | None = None) -> list[dict]:
    new = _clean_schedule(entry or default_schedule())
    new["id"] = str(uuid.uuid4())
    cleaned = [_clean_schedule(s) for s in scheds] + [new]
    set_setting("upload.schedules", json.dumps(cleaned))
    sync_cron()
    return cleaned


def delete_schedule(scheds: list[dict], sched_id: str) -> list[dict]:
    cleaned = [_clean_schedule(s) for s in scheds if s.get("id") != sched_id]
    set_setting("upload.schedules", json.dumps(cleaned))
    sync_cron()
    return cleaned


# ── Backward-compat single-schedule API (used by daily.py / cron) ────

def get_schedule() -> dict:
    return get_schedules()[0]


def save_schedule(sched: dict) -> None:
    save_schedules([sched])


def today_entry(schedule_time: str | None = None) -> dict | None:
    """Today's {topic, playlist} from matching enabled schedule, or None.

    When schedule_time is given (e.g. "19:00" or "06:00" from cron),
    only returns the schedule whose time matches. Without schedule_time
    (backward compat), returns the first enabled schedule.
    """
    for sched in get_schedules():
        if not sched["enabled"]:
            continue
        if schedule_time and sched.get("time") != schedule_time:
            continue
        key = DAYS[local_now().weekday()]
        entry = sched["days"][key]
        if entry["topic"]:
            return {**entry, "videos_per_run": sched["videos_per_run"],
                    "privacy": sched["privacy"], "time": sched["time"],
                    "format": sched.get("format", "9:16"),
                    "target_minutes": sched.get("target_minutes")}
    return None


def active_schedules() -> list[dict]:
    """All enabled schedules (for cron generator)."""
    return [s for s in get_schedules() if s["enabled"]]


def schedule_slot(time_str: str | None) -> int:
    """0-based position of this run among today's enabled schedules.

    Two runs on the same WIB day share the weekday theme, so the second slot
    needs a different sub-focus or both produce the same title.
    """
    times = sorted(s["time"] for s in active_schedules())
    try:
        return times.index(time_str)
    except (ValueError, TypeError):
        return 0


# ── Cron auto-sync ───────────────────────────────────────────────────

# The `avf` console script sits next to the running interpreter, so this works
# from a source checkout and from a packaged bundle alike.
AVF_BIN = str(Path(sys.executable).parent / "avf")
LOG_FILE = str(data_dir() / "runtime" / "logs" / "daily.log")
CRON_TAG = "# AVF daily upload"


def cron_available() -> bool:
    """Whether a saved schedule can actually be installed.

    Windows has no crontab. Until an `schtasks` backend exists, the UI warns
    instead of letting a schedule save and then silently never fire.
    """
    return shutil.which("crontab") is not None


def sync_cron() -> None:
    """Rewrite crontab to match enabled schedules.

    One cron entry per enabled schedule, matched by time.
    Preserves non-AVF cron entries.
    """
    import logging
    import subprocess

    if not cron_available():
        # Windows has no crontab; a desktop bundle would otherwise fail on every boot
        logging.getLogger("schedule").warning(
            "crontab not available — schedule saved but not installed")
        return

    # Read existing crontab, keep only non-AVF lines
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        existing = result.stdout.splitlines()
    except Exception:
        existing = []
    ours = [l for l in existing if CRON_TAG in l]
    lines = [l for l in existing if CRON_TAG not in l]

    active = active_schedules()
    if not active and not ours:
        # Nothing of ours is installed and nothing is to be installed. Writing
        # anyway would install *this* data dir's (empty) schedule list over the
        # one crontab the user has — so a bundle launched with its own data dir
        # would silently delete the entries a repo install put there.
        return

    # Generate cron entries for each enabled schedule
    for sched in active:
        time_str = sched.get("time", "09:00")
        parts = time_str.split(":")
        if len(parts) != 2:
            continue
        hour, minute = parts[0].zfill(2), parts[1].zfill(2)
        # Convert local time to UTC (assuming GMT+7)
        utc_hour = (int(hour) - 7) % 24
        entry = (
            f"{minute} {utc_hour} * * * "
            f"AVF_DAILY_VIDEOS={sched['videos_per_run']} "
            f"AVF_AUTO_APPROVE=1 "
            f"AVF_SCHEDULE_TIME={time_str} "
            f"{AVF_BIN} daily >> {LOG_FILE} 2>&1 "
            f"{CRON_TAG} {time_str} GMT+7"
        )
        lines.append(entry)

    # Write new crontab
    new_crontab = "\n".join(lines) + "\n"
    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode == 0:
            import logging
            logging.getLogger("schedule").info(
                "cron synced: %d schedule(s)", len(active))
    except Exception as e:
        import logging
        logging.getLogger("schedule").warning("cron sync failed: %s", e)
