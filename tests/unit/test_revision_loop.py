"""The revision loop: reject discards, revise regenerates with the note.

The note must reach the LLM prompts (script + storyboard) and be consumed
exactly once — the old behaviour stored it nowhere and re-rendered the same
video, which made the field a lie.
"""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv
from core.database import Database
from core.jobs import State
from core.settings import get_setting, set_setting


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db = Database(tmp_path / "avf.db")
    monkeypatch.setattr(srv, "get_db", lambda: db)
    monkeypatch.setattr(srv, "_running", set())
    with TestClient(srv.app) as c:
        yield c, db


def _project(db, status="APPROVAL"):
    iid = db.insert("ideas", topic="t")
    sid = db.insert("scripts", idea_id=iid)
    return db.insert("projects", script_id=sid, idea_id=iid, status=status)


def test_reject_discards_without_a_note(client):
    c, db = client
    pid = _project(db)
    r = c.post(f"/api/projects/{pid}/reject", json={})
    assert r.status_code == 200
    assert db.get("projects", pid)["status"] == "FAILED"


def test_revise_needs_a_note(client):
    c, db = client
    pid = _project(db)
    r = c.post(f"/api/projects/{pid}/revise", json={"reason": "   "})
    assert r.status_code == 422
    assert db.get("projects", pid)["status"] == "APPROVAL"


def test_revise_stores_note_restarts_from_script_clears_artifacts(client, tmp_path):
    c, db = client
    pid = _project(db)
    from core.filesystem import project_dir
    stale = project_dir(pid) / "rendered"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "video.mp4").write_bytes(b"old render")

    r = c.post(f"/api/projects/{pid}/revise", json={"reason": "hook too long"})
    assert r.status_code == 200
    assert r.json()["state"] == "SCRIPTING"
    assert db.get("projects", pid)["status"] == "SCRIPTING"
    assert get_setting(f"project.{pid}.revision_note") == "hook too long"
    assert not (project_dir(pid) / "rendered" / "video.mp4").exists()


def test_revision_prompt_appends_the_instruction():
    from apps.scripting.script_gen import revision_prompt
    p = revision_prompt("BASE PROMPT", "hook too long")
    assert p.startswith("BASE PROMPT")
    assert "REVISION" in p and "hook too long" in p
    # blank note → untouched prompt
    assert revision_prompt("BASE PROMPT", "   ") == "BASE PROMPT"


def test_stage_script_consumes_the_note_exactly_once(monkeypatch, tmp_path):
    from apps.orchestrator.pipeline import build_stages, get_db
    import apps.scripting.script_gen as sg

    db = get_db()
    pid = _project(db)
    set_setting(f"project.{pid}.revision_note", "shorter hook, different visuals")

    seen = {}

    def fake_generate_script(db_, idea_id, project_id=None, revision_note=""):
        seen["note"] = revision_note
        return db_.insert("scripts", idea_id=idea_id)

    monkeypatch.setattr(sg, "generate_script", fake_generate_script)
    stages = build_stages(pid)
    stages[State.SCRIPTING](pid, State.SCRIPTING)

    assert seen["note"] == "shorter hook, different visuals"
    # consumed: a resume or later run must not revise again
    assert not get_setting(f"project.{pid}.revision_note")


def test_storyboard_prompt_gets_the_note(monkeypatch, tmp_path):
    from apps.orchestrator.pipeline import get_db
    import apps.storyboard.storyboard_gen as sb

    db = get_db()
    iid = db.insert("ideas", topic="t")
    sid = db.insert("scripts", idea_id=iid, hook="h", body="b", cta="c",
                    duration=40, status="READY")

    captured = {}

    def fake_generate_parsed(task, prompt, **kw):
        captured["prompt"] = prompt
        raise RuntimeError("captured — stop before the LLM")

    monkeypatch.setattr(sb, "generate_parsed", fake_generate_parsed)
    with pytest.raises(RuntimeError, match="captured"):
        sb.generate_storyboard(db, sid, revision_note="different visuals")
    assert "REVISION" in captured["prompt"]
    assert "different visuals" in captured["prompt"]
