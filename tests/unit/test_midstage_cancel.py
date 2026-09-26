"""Mid-stage cancel: StageResult(next=CANCELLED) must stop pipeline as CANCELLED."""
from core.database import Database
from core.jobs import Orchestrator, State, StageResult


def test_next_cancelled_stops_pipeline(tmp_path):
    # file-backed DB: each call opens a fresh connection (:memory: would be empty)
    db = Database(tmp_path / "t.db")

    def stage(pid, st):
        return StageResult(stage=st, success=True,
                           data={"assets": 2}, next=State.CANCELLED)

    db.insert("projects", script_id=1, status=State.GENERATING_ASSETS.value)
    orch = Orchestrator(db, {State.GENERATING_ASSETS: stage})
    end = orch.run(1)
    assert end == State.CANCELLED
    assert db.get("projects", 1)["status"] == "CANCELLED"
