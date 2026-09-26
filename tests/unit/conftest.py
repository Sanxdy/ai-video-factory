import os
import tempfile

os.environ.setdefault("AVF_AUTH_ENABLED", "1")
# Several modules bind data_dir() at import time (log dir, models.json, YouTube
# token, music dir). Point them at a throwaway dir BEFORE any app module loads.
os.environ["AVF_DATA_DIR"] = tempfile.mkdtemp(prefix="avf-tests-")

"""Unit tests never start real background work, and never touch prod data.

TestClient(app) fires the startup hook, which spawns the queue worker; a
worker left alive could drain another test's leftover queue entries and hit
the real LLM API, racing the global LLM cache. Fresh queue per test keeps
leftovers contained.

Settings writes are redirected to a throwaway DB: several suites
(test_stock, test_schedule) call set_setting() directly — without this they
wipe real keys (pexels_key) or the upload schedule in database/factory.db.
"""
import queue

import pytest

import apps.api.server as srv


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "_run_in_background", lambda pid: None)
    monkeypatch.setattr(srv, "_ensure_queue_worker", lambda: None)
    monkeypatch.setattr(srv, "_produce_queue", queue.Queue())
    # isolate all mutable state (DB, content/, runtime/) per-test
    monkeypatch.setenv("AVF_DATA_DIR", str(tmp_path))
    # sync_cron() shells out to the real `crontab` binary, so DB isolation does
    # not contain it: a suite that saves a schedule rewrites the PRODUCTION
    # crontab from the throwaway DB, deleting the real upload slots. Neutralize it.
    import apps.uploader.schedule as sch
    monkeypatch.setattr(sch, "sync_cron", lambda: None)


@pytest.fixture(autouse=True)
def _api_auth_bypass(request, monkeypatch):
    """API tests hit the auth wall since the login gate landed. Bypass it for
    every suite EXCEPT test_auth (which tests the gate itself)."""
    if "test_auth" in request.fspath.purebasename:
        return
    from apps.api import server as srv
    monkeypatch.setattr(srv._auth, "valid_session", lambda req: True)
