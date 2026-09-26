"""Daily auto-produce job: run via cron, e.g.

  0 9 * * *  /home/ubuntu/ai-video-factory/.venv/bin/avf daily

Produces today's video from upload.schedule (topic per weekday) and runs the
full pipeline. Upload happens at APPROVAL → approve manually from the
dashboard, or set `upload.auto_approve = "1"` to publish straight away with
the schedule's privacy setting.
"""
from __future__ import annotations

from apps.uploader.schedule import today_entry
from core.config import config
from core.logging import get_logger

log = get_logger("daily")


def main() -> int:
    import os
    schedule_time = os.environ.get("AVF_SCHEDULE_TIME", "")
    entry = today_entry(schedule_time if schedule_time else None)
    if not entry:
        log.info("schedule disabled or no topic for today — skipping")
        return 0

    import sys
    from apps.uploader.schedule import local_now
    sys.path.insert(0, str(config.root))
    from apps.orchestrator.pipeline import create_project, run_pipeline
    from core.database import Database
    from core.settings import set_setting

    db = Database(config.db_path)
    count = int(os.environ.get("AVF_DAILY_VIDEOS", "2"))
    # landscape renders ~6x realtime; two of them already fill a day on this box
    ratio = entry.get("format") or "9:16"
    if ratio == "16:9":
        count = min(count, 2)
    week = local_now().isocalendar()[1]
    from apps.uploader.schedule import schedule_slot
    slot = schedule_slot(schedule_time or None)
    topics = daily_topics(entry["topic"], count, week=week, slot=slot)
    for i, topic in enumerate(topics):
        pid = create_project(topic)
        if ratio == "16:9":
            # the duration budget is what routes the script generator to the
            # long-form countdown path; it raises anything under 5 minutes.
            db.update("projects", pid, aspect_ratio="16:9")
            mins = float(entry.get("target_minutes") or 6.0)
            set_setting(f"project.{pid}.target_duration", str(mins * 60))
            set_setting(f"project.{pid}.max_scene_duration", "5")
        if entry["playlist"]:
            set_setting(f"project.{pid}.playlist", entry["playlist"])
        if entry.get("music"):
            set_setting(f"project.{pid}.music", entry["music"])
        try:
            end = run_pipeline(pid)
        except Exception as e:
            log.error("daily project %s (%d/%d) FAILED [%s]: %s",
                      pid, i + 1, count, topic[:40], e)
            continue  # move on to the next project — never abort the whole batch
        proj = db.get("projects", pid) or {}
        status = proj.get("status", "")
        log.info("daily project %s (%d/%d) [%s] → %s (%s)",
                 pid, i + 1, count, topic[:40], end, status)

        # hands-off publish (default on; set AVF_AUTO_APPROVE=0 to review first)
        if os.environ.get("AVF_AUTO_APPROVE", "1") == "1" and status == "APPROVAL":
            db.update("projects", pid, status="UPLOADING")
            try:
                run_pipeline(pid)
            except Exception as e:
                log.error("daily upload %s FAILED: %s", pid, e)
    return 0


def daily_topics(theme: str, count: int, week: int | None = None,
                 slot: int = 0) -> list[str]:
    """Videos for today. Weekly rotation: the ISO week number steers a
    sub-focus hint so week 2's videos differ from week 1's under the same
    weekday theme. Video #1 keeps the theme (with the hint); videos 2..N get
    distinct LLM-generated angles within the theme.

    slot = which run of the day this is (0 = first). Two runs share the same
    weekday theme, so the slot shifts the sub-focus to keep them distinct.
    """
    from apps.uploader.schedule import local_now
    now = local_now()
    if week is None:
        week = now.isocalendar()[1]
    focus = WEEK_FOCUSES[(week - 1 + slot) % len(WEEK_FOCUSES)]
    # Use day of week (1..7) so daily runs on same week don't produce exact same title
    day_num = now.isoweekday()
    hinted = f"{theme} (Day {day_num} - {focus})"
    if count <= 1:
        return [hinted]
    from providers.llm import generate_parsed
    try:
        parsed = generate_parsed("idea", (
            f'Generate {count - 1} DISTINCT video topics, each a different angle on '
            f'the theme "{theme}". This week\'s angle to emphasize: {focus}. '
            'They must not overlap with each other or with a video that already '
            'covers the theme generically. Output STRICT JSON: '
            '{"ideas":[{"topic": str}]}. Return only the JSON object.'),
            temperature=0.9)
        angles = [i.topic.strip() for i in getattr(parsed, "ideas", [])
                      if i.topic and i.topic.strip()][: count - 1]
    except Exception as e:
        log.warning("angle generation failed (%s) — falling back to numbered themes", e)
        angles = []
    while len(angles) < count - 1:
        angles.append(f"{theme} — part {len(angles) + 2}: fresh angle")
    return [hinted] + angles


# rotates by ISO week so the same weekday theme produces different videos weekly
WEEK_FOCUSES = [
    "the most surprising or counterintuitive examples",
    "beginner-friendly introduction for total newcomers",
    "myths and misconceptions debunked",
    "extreme records: biggest, fastest, strangest",
    "behind-the-scenes: how experts do it and what most people never see",
]


if __name__ == "__main__":
    raise SystemExit(main())
