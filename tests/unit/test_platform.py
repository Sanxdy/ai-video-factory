"""Platform behaviour that only differs off the build machine.

The Windows branches cannot execute on macOS or Linux, so what is asserted here
is the part that is platform-independent: the ctypes struct is the right shape,
the RAM contract the System page divides holds, and the schedule endpoint tells
the UI when a saved schedule cannot actually be installed.
"""
import ctypes
import shutil

import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv
# bound at import time: the autouse conftest fixture replaces the module
# attribute with a no-op (sync_cron rewrites the real crontab), and this test
# needs the real body with `crontab` itself faked.
from apps.uploader.schedule import cron_available, sync_cron


def test_win_mem_status_struct_is_64_bytes():
    """MEMORYSTATUSEX is 4+4+8*7 bytes. A wrong field list makes
    GlobalMemoryStatusEx fail and the System page render NaN."""
    st = srv._win_mem_status()
    assert ctypes.sizeof(st) == 64
    assert st._fields_[0][0] == "dwLength"  # the API reads this first


def test_mem_stats_returns_both_keys_or_neither():
    """The System page computes ram_free_mb / ram_total_mb, so a partial dict
    is worse than an empty one — it renders NaN instead of degrading."""
    mem = srv._mem_stats_mb()
    assert set(mem) in ({}, {"ram_total_mb", "ram_free_mb"})


def test_cron_available_tracks_the_binary(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert cron_available() is False  # Windows: the UI must warn, not promise
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/crontab")
    assert cron_available() is True


def _fake_crontab(monkeypatch, existing: str, schedules: list) -> list:
    """Record every `crontab -` write; return the list it was called with."""
    import subprocess
    import apps.uploader.schedule as sch
    writes: list[str] = []

    class R:
        returncode = 0
        stdout = existing

    def run(cmd, **kw):
        if cmd[1] == "-":
            writes.append(kw.get("input", ""))
        return R()

    monkeypatch.setattr(sch, "cron_available", lambda: True)
    monkeypatch.setattr(sch, "active_schedules", lambda: schedules)
    monkeypatch.setattr(subprocess, "run", run)
    sync_cron()
    return writes


def test_sync_cron_leaves_a_foreign_crontab_alone(monkeypatch):
    """A bundle launched with its own empty data dir must not install its empty
    schedule list over the one crontab the user has — that would delete the
    entries a repo install put there."""
    assert _fake_crontab(monkeypatch, "0 3 * * * /usr/bin/backup\n", []) == []


def test_sync_cron_removes_only_its_own_lines(monkeypatch):
    """The other half: once AVF has installed entries, clearing the schedules
    must remove them — and keep the user's own lines."""
    existing = ("0 3 * * * /usr/bin/backup\n"
                "00 02 * * * avf daily # AVF daily upload 09:00 GMT+7\n")
    (written,) = _fake_crontab(monkeypatch, existing, [])
    assert "/usr/bin/backup" in written
    assert "AVF daily upload" not in written


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(srv, "_ensure_queue_worker", lambda: None)
    with TestClient(srv.app) as c:
        yield c


def test_schedules_endpoint_reports_cron_availability(client, monkeypatch):
    """The Schedule settings section warns off this flag, so it has to be on the
    read path the UI loads — not on the save response."""
    assert client.get("/api/settings/schedules").json()[0]["cron_available"] is True
    import apps.uploader.schedule as sch
    monkeypatch.setattr(sch, "cron_available", lambda: False)
    assert client.get("/api/settings/schedules").json()[0]["cron_available"] is False


# ── first-launch data migration (plan risk #5) ──────────────────────────────

def _desktop():
    """Load packaging/desktop.py by path: `packaging` is also a PyPI package,
    so importing it by name would load the wrong thing (or nothing)."""
    import importlib.util
    from pathlib import Path as _P
    spec = importlib.util.spec_from_file_location(
        "avf_desktop", _P(__file__).resolve().parents[2] / "packaging" / "desktop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _checkout(root, db="REAL"):
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "config.py").write_text("")
    (root / "database").mkdir(parents=True, exist_ok=True)
    (root / "database" / "factory.db").write_text(db)
    (root / "content" / "rendered").mkdir(parents=True, exist_ok=True)
    (root / "content" / "rendered" / "v.mp4").write_text("video")
    return root


def test_first_launch_copies_an_existing_install(tmp_path, monkeypatch):
    """A bundle must inherit an existing checkout's projects, once.

    Without this, installing the app shows an empty dashboard and the user's
    real projects look lost — the bundle's data dir is the platform one, not
    the repo.
    """
    desktop = _desktop()
    src = _checkout(tmp_path / "repo")
    monkeypatch.setenv("AVF_MIGRATE_FROM", str(src))
    dest = tmp_path / "data"
    desktop._migrate(dest)
    assert (dest / "database" / "factory.db").read_text() == "REAL"
    assert (dest / "content" / "rendered" / "v.mp4").exists()


def test_migration_never_overwrites_live_data(tmp_path, monkeypatch):
    desktop = _desktop()
    monkeypatch.setenv("AVF_MIGRATE_FROM", str(_checkout(tmp_path / "repo")))
    dest = tmp_path / "data"
    (dest / "database").mkdir(parents=True)
    (dest / "database" / "factory.db").write_text("NEWER")
    desktop._migrate(dest)
    assert (dest / "database" / "factory.db").read_text() == "NEWER"


def test_migration_ignores_a_folder_that_is_not_a_checkout(tmp_path, monkeypatch):
    """core/config.py is the tell — a stray database/ is not an install."""
    desktop = _desktop()
    fake = tmp_path / "not-a-checkout"
    (fake / "database").mkdir(parents=True)
    (fake / "database" / "factory.db").write_text("x")
    bundle = fake / "packaging" / "out" / "avf-linux-x64"
    bundle.mkdir(parents=True)
    monkeypatch.delenv("AVF_MIGRATE_FROM", raising=False)
    monkeypatch.setattr(desktop, "BUNDLE", bundle)
    dest = tmp_path / "data"
    desktop._migrate(dest)
    assert not (dest / "database").exists()
