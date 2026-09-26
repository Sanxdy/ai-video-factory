"""DELETE /api/projects/{pid}: cascades DB rows, refuses running pipeline."""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv
from core.database import Database


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db = Database(tmp_path / "avf.db")
    monkeypatch.setattr(srv, "get_db", lambda: db)
    monkeypatch.setattr(srv, "_running", set())
    with TestClient(srv.app) as c:
        yield c, db


def _seed(db):
    iid = db.insert("ideas", topic="t")
    rid = db.insert("research", idea_id=iid)
    sid = db.insert("scripts", idea_id=iid)
    sc = db.insert("scenes", script_id=sid, scene_number=1)
    db.insert("assets", scene_id=sc, type="image")
    vid = db.insert("videos", script_id=sid)
    up = db.insert("uploads", video_id=vid)
    an = db.insert("analytics", video_id=vid)
    pid = db.insert("projects", script_id=sid, idea_id=iid)
    return pid, {"ideas": iid, "research": rid, "scripts": sid,
                 "scenes": sc, "assets": 1, "videos": vid,
                 "uploads": up, "analytics": an}


def test_delete_cascades(client):
    c, db = client
    pid, ids = _seed(db)
    r = c.delete(f"/api/projects/{pid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    for table in ("ideas", "research", "scripts", "scenes", "assets",
                  "videos", "projects"):
        assert db.all(table) == [], f"{table} not emptied"
    # analytics + uploads survive — YouTube stats persist after gallery delete
    assert db.all("analytics") != [], "analytics should survive gallery delete"
    assert db.all("uploads") != [], "uploads should survive gallery delete"


def test_delete_missing_and_running(client):
    c, db = client
    assert c.delete("/api/projects/999").status_code == 404
    pid, _ = _seed(db)
    srv._running.add(pid)
    try:
        assert c.delete(f"/api/projects/{pid}").status_code == 409
    finally:
        srv._running.discard(pid)
    assert db.get("projects", pid) is not None
