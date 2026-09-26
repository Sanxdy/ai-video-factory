"""Tests: daily upload schedule. Uses an isolated settings DB — never prod."""
import json

import pytest

import core.settings as cs
from apps.uploader.schedule import get_schedule, save_schedule, today_entry, default_schedule


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # conftest points AVF_DATA_DIR at tmp_path, so the settings DB is already
    # isolated — only the by-name import in schedule.py needs rebinding
    import apps.uploader.schedule as sch
    monkeypatch.setattr(sch, "get_setting",
                        lambda k, d=None: cs.get_setting(k, d))


def test_roundtrip():
    s = default_schedule()
    s["enabled"] = True
    s["days"]["mon"] = {"topic": "Cat comedy", "playlist": "Cat Videos"}
    save_schedule(s)
    out = get_schedule()
    assert out["enabled"] is True
    assert out["days"]["mon"]["topic"] == "Cat comedy"
    assert out["days"]["mon"]["playlist"] == "Cat Videos"
    assert out["privacy"] in ("public", "unlisted", "private")


def test_bad_json_falls_back(monkeypatch):
    import apps.uploader.schedule as sch
    monkeypatch.setattr(sch, "get_setting", lambda k: "{broken")
    s = get_schedule()
    assert s["enabled"] is False and set(s["days"]) == {
        "mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def test_today_entry_disabled():
    save_schedule(default_schedule())  # disabled (isolated DB)
    assert today_entry() is None


def test_today_entry_uses_wib_not_utc(monkeypatch):
    """06:00 WIB is 23:00 UTC the day before — the weekday must follow WIB,
    otherwise the early run picks up yesterday's theme (seen on 2026-09-13)."""
    from datetime import datetime, timedelta, timezone
    import apps.uploader.schedule as sch

    # Saturday 2026-09-12 23:00 UTC == Sunday 2026-09-13 06:00 WIB
    utc_moment = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sch, "local_now", lambda: utc_moment + timedelta(hours=7))
    assert utc_moment.weekday() == 5  # Saturday in UTC — the old buggy answer

    s = default_schedule()
    s["enabled"] = True
    s["time"] = "06:00"
    s["days"]["sat"] = {"topic": "Saturday theme", "playlist": "", "music": ""}
    s["days"]["sun"] = {"topic": "Sunday theme", "playlist": "", "music": ""}
    save_schedule(s)

    entry = today_entry("06:00")
    assert entry is not None
    assert entry["topic"] == "Sunday theme"


def test_disabling_every_schedule_stays_disabled():
    """The setting is the source of truth. Switching both slots off must leave
    them off — not resurrect the v1 `upload.schedule`, which also lost its
    videos_per_run (2 → 1) when it did (seen 2026-09-24)."""
    import apps.uploader.schedule as sch
    # a legacy schedule exists and looks usable — the old fallback would use it
    cs.set_setting("upload.schedule", json.dumps(
        {**default_schedule(), "enabled": True, "time": "19:00",
         "days": {d: {"topic": "legacy topic", "playlist": "", "music": ""}
                  for d in sch.DAYS}}))
    s19 = default_schedule(); s19.update(enabled=False, time="19:00")
    s06 = default_schedule(); s06.update(enabled=False, time="06:00")
    sch.save_schedules([s19, s06])
    assert sch.active_schedules() == []
    assert sch.today_entry("19:00") is None
    assert [s["enabled"] for s in sch.get_schedules()] == [False, False]


def test_absent_key_migrates_from_legacy():
    """A v1 install has only `upload.schedule`; it must still be picked up."""
    import apps.uploader.schedule as sch
    cs.set_setting("upload.schedule", json.dumps(
        {**default_schedule(), "enabled": True, "time": "19:00",
         "videos_per_run": 2,
         "days": {d: {"topic": "legacy topic", "playlist": "", "music": ""}
                  for d in sch.DAYS}}))
    got = sch.get_schedules()
    assert len(got) == 1 and got[0]["enabled"] is True
    assert got[0]["videos_per_run"] == 2      # preserved, not defaulted to 1


def test_enabled_schedule_is_not_dropped_when_saved():
    """Regression: an enabled schedule with topics must survive a save."""
    import apps.uploader.schedule as sch
    s = default_schedule()
    s.update(enabled=True, time="19:00", videos_per_run=2)
    s["days"]["mon"] = {"topic": "Cat comedy", "playlist": "Cats", "music": ""}
    sch.save_schedules([s])
    assert len(sch.active_schedules()) == 1


# ── long-form format on a schedule ────────────────────────────────

def test_legacy_schedule_reads_as_portrait():
    """A schedule saved before long-form existed has no `format`. It must come
    back as 9:16 and keep producing exactly what it produced before."""
    import apps.uploader.schedule as sch
    legacy = {"id": "old", "enabled": True, "time": "19:00", "videos_per_run": 4,
              "privacy": "public",
              "days": {d: {"topic": "t", "playlist": "p", "music": ""}
                       for d in sch.DAYS}}
    cleaned = sch._clean_schedule(legacy)
    assert cleaned["format"] == "9:16"
    assert cleaned["videos_per_run"] == 4          # 20-cap, not the 2-cap


def test_landscape_caps_videos_per_run_at_two():
    """Landscape renders ~6x realtime; 20 of them would be 11 hours."""
    import apps.uploader.schedule as sch
    cleaned = sch._clean_schedule({**default_schedule(), "format": "16:9",
                                   "videos_per_run": 20})
    assert cleaned["videos_per_run"] == 2


def test_landscape_target_minutes_is_clamped_to_at_least_five():
    import apps.uploader.schedule as sch
    assert sch._clean_schedule({**default_schedule(), "format": "16:9",
                                "target_minutes": 1})["target_minutes"] == 5.0
    assert sch._clean_schedule({**default_schedule(), "format": "16:9",
                                "target_minutes": 99})["target_minutes"] == 20.0


def test_unknown_format_falls_back_to_portrait():
    import apps.uploader.schedule as sch
    assert sch._clean_schedule({**default_schedule(),
                                "format": "4:3"})["format"] == "9:16"


def test_today_entry_carries_format_and_target(monkeypatch):
    """daily.py reads these off the entry to tag the project it creates."""
    import apps.uploader.schedule as sch
    s = default_schedule()
    s.update(enabled=True, time="10:00", format="16:9", target_minutes=7)
    # every weekday, so the assertion holds whatever day the suite runs on
    for d in sch.DAYS:
        s["days"][d] = {"topic": "Extreme places", "playlist": "Countdowns",
                        "music": ""}
    save_schedule(s)
    entry = sch.today_entry("10:00")
    assert entry is not None
    assert entry["format"] == "16:9"
    assert entry["target_minutes"] == 7
