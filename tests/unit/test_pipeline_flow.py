"""Unit tests for pipeline flow: cancel, state events, user-topic projects."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core import database as db_mod
from core.config import config
from core.database import Database
from core.jobs import Orchestrator, State, can_transition, is_cancel_requested, request_cancel
import apps.orchestrator.pipeline as pipe


@pytest.fixture()
def db(tmp_path):
    return db_mod.Database(tmp_path / "test.db")


def _stages_ok():
    """Two passing stages."""
    def s1(pid, st):
        from core.jobs import StageResult
        return StageResult(stage=st, success=True)
    return {State.IDEA: s1, State.RESEARCHING: s1}


def test_cancel_transition_rules():
    assert can_transition(State.IDEA, State.CANCELLED)
    assert can_transition(State.EDITING, State.CANCELLED)
    assert not can_transition(State.COMPLETE, State.CANCELLED)
    # re-run after cancel resumes forward
    assert can_transition(State.CANCELLED, State.RESEARCHING)
    assert not can_transition(State.CANCELLED, State.IDEA)


def test_orchestrator_cancels_between_stages(db):
    pid = db.insert("projects", script_id=0, status="IDEA", created_at="now")
    orch = Orchestrator(db, _stages_ok())
    request_cancel(pid)
    assert is_cancel_requested(pid)
    end = orch.run(pid)
    assert end == State.CANCELLED
    assert db.get("projects", pid)["status"] == "CANCELLED"
    assert not is_cancel_requested(pid)  # consumed


def test_orchestrator_emits_state_events(db):
    from core import events
    seen = []
    events.subscribe("project_state", lambda **d: seen.append(d))
    pid = db.insert("projects", script_id=0, status="IDEA", created_at="now")
    Orchestrator(db, _stages_ok()).run(pid)
    states = [d["state"] for d in seen if d["project_id"] == pid]
    # handler-less stages auto-advance; APPROVAL is the human-gate pause
    assert "RESEARCHING" in states and "APPROVAL" in states


def test_create_project_with_topic_seeds_idea(tmp_path, monkeypatch):
    pid = pipe.create_project("Why cats purr")
    db = Database(config.db_path)
    proj = db.get("projects", pid)
    idea = db.get("ideas", proj["idea_id"])
    assert idea["topic"] == "Why cats purr"
    assert idea["source"] == "user"
    assert idea["status"] == "NEW"  # stage_idea claims it later


def test_incomplete_projects_excludes_terminal(db):
    pid = db.insert("projects", script_id=0, status="CANCELLED", created_at="now")
    ids = [p["id"] for p in db.incomplete_projects()]
    assert pid not in ids
