"""Archive, clone, and approve publish_at validation."""
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


def _project(db, topic="t", status="APPROVAL"):
    iid = db.insert("ideas", topic=topic)
    sid = db.insert("scripts", idea_id=iid)
    pid = db.insert("projects", script_id=sid, idea_id=iid, status=status)
    return pid


def test_archive_hides_from_list(client):
    c, db = client
    pid = _project(db)
    assert c.post(f"/api/projects/{pid}/archive",
                  json={"archived": True}).json()["archived"] is True
    assert c.get("/api/projects").json() == []
    rows = c.get("/api/projects?include_archived=1").json()
    assert len(rows) == 1 and rows[0]["archived"] == 1
    # restore
    c.post(f"/api/projects/{pid}/archive", json={"archived": False})
    assert len(c.get("/api/projects").json()) == 1


def test_clone_copies_chain_and_files(client, monkeypatch, tmp_path):
    c, db = client
    from core import filesystem
    monkeypatch.setattr(srv, "_run_in_background", lambda pid: None)
    src_dir = filesystem.project_dir(1)
    (src_dir / "assets").mkdir(parents=True)
    (src_dir / "assets" / "scene-01.mp4").write_bytes(b"x")
    (src_dir / "audio").mkdir()
    (src_dir / "audio" / "scene-01.wav").write_bytes(b"y")
    pid = _project(db, topic="Orig", status="APPROVAL")
    r = c.post("/api/projects/1/clone")
    assert r.status_code == 200
    new_pid = r.json()["project"]
    row = db.get("projects", new_pid)
    assert row["status"] == "EDITING" and row["id"] != 1
    idea = db.get("ideas", row["idea_id"])
    assert idea["topic"] == "Orig (clone)"
    scenes = db.all("scenes", "script_id=?", (row["script_id"],))
    # source has no scenes (only script) — clone still valid, files copied
    from pathlib import Path
    dst = Path("content") / "projects" / f"project-{new_pid:05d}"
    # project_dir used config ROOT — patched ROOT means tmp paths
    dst_dir = tmp_path / "content" / "projects" / f"project-{new_pid:05d}"
    assert (dst_dir / "assets" / "scene-01.mp4").exists()


def test_approve_publish_at_validation(client, monkeypatch, tmp_path):
    c, db = client
    monkeypatch.setattr("apps.uploader.youtube.TOKEN_PATH", tmp_path / "tok.json")
    pid = _project(db)
    r = c.post(f"/api/projects/{pid}/approve", json={"publish_at": "not-a-date"})
    assert r.status_code == 422
    r = c.post(f"/api/projects/{pid}/approve",
               json={"publish_at": "2020-01-01T00:00:00Z"})
    assert r.status_code == 422  # past
