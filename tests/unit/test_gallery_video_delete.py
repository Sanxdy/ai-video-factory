"""Gallery delete removes the rendered video only — the project must survive.

Retention cleanup: published projects (youtube_id) keep their dashboard record
and only lose local media.
"""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv
from core import cleanup
from core.config import config
from core.database import Database


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db = Database(tmp_path / "avf.db")
    monkeypatch.setattr(srv, "get_db", lambda: db)
    monkeypatch.setattr(srv, "_running", set())
    monkeypatch.setattr(config, "root", tmp_path)
    with TestClient(srv.app) as c:
        yield c, db, tmp_path


def _seed(db, tmp_path, youtube_id=""):
    iid = db.insert("ideas", topic="t")
    sid = db.insert("scripts", idea_id=iid)
    pid = db.insert("projects", script_id=sid, idea_id=iid, youtube_id=youtube_id,
                    created_at="2020-01-01T00:00:00+00:00", status="COMPLETE")
    rdir = tmp_path / "content" / "rendered" / f"project-{pid:05d}"
    pdir = tmp_path / "content" / "projects" / f"project-{pid:05d}"
    rdir.mkdir(parents=True); (rdir / "final.mp4").write_bytes(b"x" * 100)
    pdir.mkdir(parents=True); (pdir / "manifest.json").write_text("{}")
    return pid, rdir, pdir


def test_gallery_delete_keeps_project(env):
    c, db, tmp_path = env
    pid, rdir, pdir = _seed(db, tmp_path)

    r = c.delete(f"/api/projects/{pid}/video")
    assert r.status_code == 200 and r.json()["freed_bytes"] > 0

    assert db.get("projects", pid) is not None, "project row must survive"
    assert db.get("ideas", db.get("projects", pid)["idea_id"]) is not None
    assert not rdir.exists(), "rendered video must be gone"
    assert pdir.exists(), "project source dir kept so a re-render is possible"

    # and it still shows on the dashboard, just without a video
    row = next(p for p in c.get("/api/projects").json() if p["id"] == pid)
    assert row["has_video"] is False


def test_gallery_bulk_delete_keeps_projects(env):
    c, db, tmp_path = env
    p1, r1, _ = _seed(db, tmp_path)
    p2, r2, _ = _seed(db, tmp_path)

    r = c.post("/api/projects/bulk-delete-videos", json={"ids": [p1, p2]})
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert db.get("projects", p1) and db.get("projects", p2)
    assert not r1.exists() and not r2.exists()


def test_gallery_delete_missing_and_running(env):
    c, db, tmp_path = env
    assert c.delete("/api/projects/999/video").status_code == 404
    pid, _, _ = _seed(db, tmp_path)
    srv._running.add(pid)
    try:
        assert c.delete(f"/api/projects/{pid}/video").status_code == 409
    finally:
        srv._running.discard(pid)


def test_dashboard_sorts_by_date_not_id(env):
    """Restored YouTube records have old dates but fresh ids — the dashboard
    must not bury today's projects under them."""
    c, db, tmp_path = env
    old, _, _ = _seed(db, tmp_path)          # created_at 2020
    new_iid = db.insert("ideas", topic="fresh")
    new = db.insert("projects", script_id=0, idea_id=new_iid, status="COMPLETE",
                    created_at="2026-09-13T12:00:00+00:00")

    rows = c.get("/api/projects").json()
    assert new > old, "fixture sanity: fresh project has the higher id"
    assert [r["id"] for r in rows][0] == new


def test_retention_keeps_published_record(env):
    c, db, tmp_path = env
    pub, rdir_pub, pdir_pub = _seed(db, tmp_path, youtube_id="abc123")
    draft, rdir_draft, pdir_draft = _seed(db, tmp_path)

    res = cleanup.run_retention_cleanup(db, days=7)

    assert res["trimmed_count"] == 1 and res["deleted_count"] == 1
    assert db.get("projects", pub) is not None, "published project keeps its row"
    assert db.get("projects", draft) is None
    assert not rdir_pub.exists() and not pdir_pub.exists()
