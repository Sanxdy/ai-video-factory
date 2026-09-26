"""Retry derivation: assets+narration on disk → EDITING (assembly-only)."""
import pytest

from core.config import config, data_dir


@pytest.fixture()
def project_with_assets(tmp_path, monkeypatch):
    """Project with scenes rows, per-scene asset files, narration.wav."""
    # everything isolated to tmp_path — the conftest autouse fixture points
    # AVF_DATA_DIR here, so config.db_path and data_dir() both resolve inside it
    import apps.api.server as srv
    from core.database import Database
    db = Database(config.db_path)
    monkeypatch.setattr(srv, "get_db", lambda: db)
    sid = db.insert("scripts", idea_id=0, version=1, hook="", body="", cta="",
                    duration=10, status="READY", created_at="2026-01-01T00:00:00")
    pid = db.insert("projects", script_id=sid, status="APPROVAL",
                    created_at="2026-01-01T00:00:00")
    pdir = tmp_path / "content" / "projects" / f"project-{pid:05d}"
    (pdir / "assets").mkdir(parents=True)
    for n in (1, 2):
        db.insert("scenes", script_id=sid, scene_number=n, narration=f"n{n}",
                  visual_prompt=f"v{n}", duration=3.0, motion_prompt="zoom-in",
                  text_overlay="", asset_type="video", status="ASSET_READY")
        (pdir / "assets" / f"scene-{n:02d}.mp4").write_bytes(b"x")
    # rebuild needs the previous render's voice track + burned-in subs source
    rdir = tmp_path / "content" / "rendered" / f"project-{pid:05d}"
    rdir.mkdir(parents=True)
    (rdir / "narration.wav").write_bytes(b"x")
    (pdir / "subtitles.ass").write_text("[script]", encoding="utf-8")
    yield db, pid


def test_retry_full_assets_goes_editing(project_with_assets):
    from fastapi.testclient import TestClient
    from apps.api.server import app
    db, pid = project_with_assets
    with TestClient(app) as c:
        r = c.post(f"/api/projects/{pid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "EDITING"
    assert db.get("projects", pid)["status"] == "EDITING"


def test_retry_missing_asset_falls_through(project_with_assets):
    from fastapi.testclient import TestClient
    from apps.api.server import app
    db, pid = project_with_assets
    (data_dir() / "content" / "projects" / f"project-{pid:05d}"
     / "assets" / "scene-02.mp4").unlink()
    with TestClient(app) as c:
        r = c.post(f"/api/projects/{pid}/retry")
    assert r.status_code == 200
    assert r.json()["state"] != "EDITING"


def test_retry_rendered_video_goes_quality_check(project_with_assets):
    """Regression: the QUALITY_CHECK branch read p['has_video'], a serializer
    field that never exists on the raw DB row — dead code since inception."""
    from fastapi.testclient import TestClient
    from apps.api.server import app
    db, pid = project_with_assets
    db.update("projects", pid, status="FAILED")
    # video on disk but no voice track → can't assemble, only re-check
    (data_dir() / "content" / "rendered" / f"project-{pid:05d}"
     / "narration.wav").unlink()
    (data_dir() / "content" / "rendered" / f"project-{pid:05d}"
     / "final.mp4").write_bytes(b"x")
    with TestClient(app) as c:
        r = c.post(f"/api/projects/{pid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "QUALITY_CHECK"


def test_retry_after_cancel_clears_stale_flag(project_with_assets):
    """Regression: cancel raised the cooperative flag while nothing was
    running; the next run honored the stale flag and instantly re-cancelled."""
    from fastapi.testclient import TestClient
    from core.jobs import is_cancel_requested
    from apps.api.server import app
    db, pid = project_with_assets
    with TestClient(app) as c:
        assert c.post(f"/api/projects/{pid}/cancel").json()["mode"] == "cancelled"
        assert is_cancel_requested(pid)
        r = c.post(f"/api/projects/{pid}/retry")
    assert r.status_code == 200 and r.json()["state"] == "EDITING"
    assert not is_cancel_requested(pid)


def test_retry_complete_only_as_clip_swap(project_with_assets):
    from fastapi.testclient import TestClient
    import shutil
    from apps.api.server import app
    db, pid = project_with_assets
    db.update("projects", pid, status="COMPLETE")
    with TestClient(app) as c:
        # full assembly on disk → allowed, straight to EDITING
        assert c.post(f"/api/projects/{pid}/retry").json()["state"] == "EDITING"
        db.update("projects", pid, status="COMPLETE")
        # without the previous render's voice track → not rebuildable
        shutil.rmtree(data_dir() / "content" / "rendered" /
                      f"project-{pid:05d}", ignore_errors=True)
        assert c.post(f"/api/projects/{pid}/retry").status_code == 409


def test_upload_scene_asset_and_validation(project_with_assets):
    from fastapi.testclient import TestClient
    from apps.api.server import app
    _, pid = project_with_assets
    with TestClient(app) as c:
        ok = c.post(f"/api/projects/{pid}/scenes/2/asset",
                    files={"file": ("clip.mp4", b"FAKEMP4", "video/mp4")})
        assert ok.status_code == 200
        p = c.get(f"/api/projects/{pid}").json()
        scene2 = next(s for s in p["scenes"] if s["scene_number"] == 2)
        assert scene2["has_video"] is True
        bad = c.post(f"/api/projects/{pid}/scenes/2/asset",
                     files={"file": ("x.gif", b"GIF", "image/gif")})
        assert bad.status_code == 422
        gone = c.delete(f"/api/projects/{pid}/scenes/2/asset")
        assert gone.status_code == 200
        p2 = c.get(f"/api/projects/{pid}").json()
        scene2b = next(s for s in p2["scenes"] if s["scene_number"] == 2)
        assert scene2b["has_video"] is False
