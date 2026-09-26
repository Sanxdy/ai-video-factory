"""daily.py must apply a schedule's format to every project it creates.

This is the wiring between the schedule's new `format` field and the long-form
pipeline: a landscape schedule has to tag its project 16:9 and give it a
duration budget, or the script generator stays on the Shorts path and the
video comes out vertical.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

import core.settings as cs
import apps.uploader.schedule as sch


class _FakeDB:
    """Records the column updates daily.py makes on a new project."""
    def __init__(self, *a, **k):
        self.updates = []

    def update(self, table, pid, **cols):
        self.updates.append((pid, cols))

    def get(self, table, pid):
        return {"status": "APPROVAL"}


@pytest.fixture()
def store(monkeypatch):
    """ONE in-memory settings store shared by every read and write."""
    s = {}
    monkeypatch.setattr(cs, "get_setting", lambda k, d=None: s.get(k, d))
    monkeypatch.setattr(cs, "set_setting", lambda k, v: s.__setitem__(k, v))
    monkeypatch.setattr(sch, "get_setting", lambda k, d=None: s.get(k, d))
    monkeypatch.setattr(sch, "set_setting", lambda k, v: s.__setitem__(k, v))
    monkeypatch.setattr(sch, "sync_cron", lambda: None)   # never touch crontab
    monkeypatch.delenv("AVF_SCHEDULE_TIME", raising=False)
    monkeypatch.setenv("AVF_DAILY_VIDEOS", "1")           # one video unless a test says otherwise
    monkeypatch.setenv("AVF_AUTO_APPROVE", "0")           # keep the upload path out of this test
    return s


def _run_daily(monkeypatch, store, entry_format, minutes, videos=1):
    """Run daily.main() with the pipeline stubbed out."""
    created = []
    fake_db = _FakeDB()

    import apps.uploader.daily as daily
    import apps.orchestrator.pipeline as pipeline
    import core.database as cdb
    from core.jobs import State

    monkeypatch.setattr(pipeline, "create_project",
                        lambda topic=None: (created.append(topic), 900 + len(created))[1])
    monkeypatch.setattr(pipeline, "run_pipeline", lambda pid, **k: State.APPROVAL)
    monkeypatch.setattr(cdb, "Database", lambda *a, **k: fake_db)

    # an enabled schedule with a topic for every weekday, so the test is
    # independent of the day the suite runs on
    s = sch.default_schedule()
    s.update(enabled=True, time="10:00", format=entry_format,
             target_minutes=minutes, videos_per_run=videos)
    for d in sch.DAYS:
        s["days"][d] = {"topic": "Extreme places", "playlist": "Countdowns",
                        "music": ""}
    sch.save_schedules([s])
    daily.main()
    return created, store, fake_db


def test_landscape_schedule_tags_projects_as_long_form(monkeypatch, store):
    created, settings, db = _run_daily(monkeypatch, store, "16:9", 7)
    assert len(created) == 1
    pid = 901
    assert (pid, {"aspect_ratio": "16:9"}) in db.updates
    assert float(settings[f"project.{pid}.target_duration"]) == 7 * 60
    assert settings[f"project.{pid}.max_scene_duration"] == "5"
    # the playlist from the schedule still lands on the project
    assert settings[f"project.{pid}.playlist"] == "Countdowns"


def test_portrait_schedule_does_not_tag_or_budget(monkeypatch, store):
    """The Shorts path must be untouched: no aspect_ratio write, no budget."""
    created, settings, db = _run_daily(monkeypatch, store, "9:16", 6)
    assert len(created) == 1
    assert db.updates == []
    assert "project.901.target_duration" not in settings


def test_landscape_schedule_caps_videos_per_run(monkeypatch, store):
    """A landscape run is capped at 2 even if the env asks for more."""
    monkeypatch.setenv("AVF_DAILY_VIDEOS", "5")
    created, _, _ = _run_daily(monkeypatch, store, "16:9", 6, videos=2)
    assert len(created) == 2
