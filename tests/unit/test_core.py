"""Unit tests for core: config, database, state machine, models, filesystem."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core import database as db_mod
from core.config import Config
from core.filesystem import AssetCache, cache_key, ensure_dirs, project_dir
from core.jobs import Orchestrator, State, can_transition, next_state
from core.models import IdeasOutput, parse_model


@pytest.fixture()
def db(tmp_path):
    return db_mod.Database(tmp_path / "test.db")


def test_config_loads():
    cfg = Config()
    assert cfg.project_name == "ai-video-factory"
    assert cfg.get("video", "height") == 1920
    assert cfg.get("cost", "paid_api_allowed") is False


def test_db_crud(db):
    cid = db.insert("channels", name="test_ch", niche="science", created_at="now")
    assert cid > 0
    ch = db.get("channels", cid)
    assert ch["name"] == "test_ch"
    db.update("channels", cid, active=0)
    assert db.get("channels", cid)["active"] == 0


def test_db_jobs(db):
    jid = db.create_job("test_job", entity_id=1)
    db.set_job(jid, "RUNNING")
    db.set_job(jid, "SUCCESS")
    assert db.job_status(jid)["status"] == "SUCCESS"
    assert db.job_status(jid)["finished_at"] is not None


def test_db_retry_attempts(db):
    jid = db.create_job("retry_job")
    db.set_job(jid, "RETRYING")
    db.set_job(jid, "RETRYING")
    assert db.job_status(jid)["attempts"] == 2


def test_state_machine_order():
    assert next_state(State.IDEA) == State.RESEARCHING
    assert next_state(State.RESEARCHED) == State.SCRIPTING
    assert next_state(State.COMPLETE) is None
    assert can_transition(State.IDEA, State.FAILED)
    assert can_transition(State.RESEARCHING, State.RESEARCHED)
    assert not can_transition(State.COMPLETE, State.IDEA)


def test_state_machine_full_pipeline():
    """Whole pipeline must be linear and cover every non-terminal state.

    APPROVAL is a human gate: next_state pauses there (returns None), so the
    walk covers everything up to APPROVAL; UPLOADED+ are post-approval.
    """
    s = State.IDEA
    visited = []
    while s is not None:
        visited.append(s)
        s = next_state(s)
    assert set(visited) | {State.UPLOADING, State.UPLOADED, State.ANALYTICS,
                           State.COMPLETE} == \
        {st for st in State if st not in (State.FAILED, State.CANCELLED)}
    # human gate: pipeline must NOT auto-advance past APPROVAL
    assert next_state(State.APPROVAL) is None


def test_orchestrator_runs_pipeline(db, monkeypatch):
    calls = []

    def fake_stage(project_id, state):
        calls.append(state)
        from core.jobs import StageResult
        return StageResult(stage=state, success=True, data={})

    stages = {s: fake_stage for s in State}
    pid = db.insert("projects", script_id=1, status="IDEA")
    orch = Orchestrator(db, stages)
    end = orch.run(pid, start_at=State.IDEA)
    # pipeline pauses at the APPROVAL human gate
    assert end == State.APPROVAL
    assert db.get("projects", pid)["status"] == "APPROVAL"
    assert len(calls) == len([s for s in State if s not in (State.FAILED, State.CANCELLED)]) - 4


def test_orchestrator_failure_detects(db):
    from core.jobs import StageResult

    def bad_stage(project_id, state):
        return StageResult(stage=state, success=False, error="boom")

    stages = {State.RESEARCHING: bad_stage}
    pid = db.insert("projects", script_id=1, status="IDEA")
    orch = Orchestrator(db, stages)
    with pytest.raises(RuntimeError, match="boom"):
        orch.run(pid, start_at=State.RESEARCHING)
    assert db.get("projects", pid)["status"] == "FAILED"


def test_orchestrator_resume(db):
    """Project stuck mid-pipeline resumes from current state."""
    from core.jobs import StageResult

    touched = []

    def stage(project_id, state):
        touched.append(state)
        return StageResult(stage=state, success=True, data={})

    stages = {s: stage for s in State}
    pid = db.insert("projects", script_id=1, status="RENDERED")  # already rendered
    orch = Orchestrator(db, stages)
    end = orch.run(pid)
    assert end == State.APPROVAL  # pauses at human gate
    assert State.RENDERED in touched  # resumed from where it stopped


def test_model_parse_idea():
    raw = json.dumps({"ideas": [{
        "topic": "Octopus brains", "hook": "they have 9 brains",
        "reason": "shock value", "estimated_interest": 90, "difficulty": 30,
    }]})
    out = parse_model("idea", raw)
    assert isinstance(out, IdeasOutput)
    assert out.ideas[0].estimated_interest == 90


def test_model_parse_quality():
    raw = json.dumps({"score": 88, "pass": True, "issues": [], "recommendations": []})
    out = parse_model("quality", raw)
    assert out.pass_ is True


def test_model_invalid_rejected():
    with pytest.raises(Exception):
        parse_model("idea", '{"ideas": [{"topic": "x"}]}')  # missing required fields


def test_cache_keying():
    assert cache_key("a", "b") == cache_key("a", "b")
    assert cache_key("a", "b") != cache_key("a", "c")


def test_asset_cache(tmp_path):
    c = AssetCache(tmp_path)
    p = c.put("k1", b"data", ".png")
    assert p.exists()
    assert c.get("k1", ".png") == p
    assert c.get("missing", ".png") is None


def test_dirs_and_project(tmp_path, monkeypatch):
    # conftest points AVF_DATA_DIR at tmp_path, so data_dir() already resolves here
    ensure_dirs()
    p = project_dir(1)
    assert p.exists()