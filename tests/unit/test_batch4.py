"""Batch-4: title variants, script payload, music bed, serial batch queue."""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv
from core.config import config
from core.database import Database


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db = Database(config.db_path)
    # POST /api/produce fails fast (409) without an API key or a stock key.
    # These suites are about the queue, not about readiness, so store both.
    from core.settings import set_setting
    set_setting("llm.api_key", "test-key")
    set_setting("image.pexels_key", "test-key")
    monkeypatch.setattr(srv, "get_db", lambda: db)
    # create_project lives in the pipeline module — same DB, same data_dir()
    monkeypatch.setattr(srv, "_running", set())
    # never let a real queue worker consume enqueued projects during tests
    monkeypatch.setattr(srv, "_ensure_queue_worker", lambda: None)
    with TestClient(srv.app) as c:
        yield c, db


def _project(db):
    iid = db.insert("ideas", topic="T")
    sid = db.insert("scripts", idea_id=iid)
    return db.insert("projects", script_id=sid, idea_id=iid)


def test_suggest_title_returns_scored_variants(client, monkeypatch):
    c, db = client
    monkeypatch.setattr("apps.research.title_gen.generate_titles",
                        lambda t, h="": {
                            "titles": [{"title": "A", "score": 9, "why": "curiosity"},
                                       {"title": "B", "score": 7}],
                            "description": "d", "hashtags": [],
                            "best": {"title": "A", "score": 9}})
    pid = _project(db)
    r = c.post(f"/api/projects/{pid}/suggest-title").json()
    assert r["titles"][0] == {"title": "A", "score": 9, "why": "curiosity"}
    assert r["titles"][1]["score"] == 7 and "why" in r["titles"][1]


def test_project_detail_script_and_music(client):
    c, db = client
    pid = _project(db)
    db.get("scripts", db.get("projects", pid)["script_id"])
    d = c.get(f"/api/projects/{pid}").json()
    assert set(d["script"]) == {"hook", "body", "cta"}
    assert d["music"] == ""


def test_music_list_save_and_validation(client, monkeypatch, tmp_path):
    c, db = client
    mdir = tmp_path / "content" / "music"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "calm.mp3").write_bytes(b"x")
    (mdir / "notes.txt").write_text("ignore me")
    # the endpoint lists the user's folder plus the beds shipped in assets/music,
    # so assert on what this test controls: the .mp3 appears, the .txt does not
    listed = c.get("/api/music").json()
    assert "calm.mp3" in listed and "notes.txt" not in listed
    assert listed == sorted(listed)
    pid = _project(db)
    assert c.post(f"/api/projects/{pid}/music",
                  json={"track": "nope.mp3"}).status_code == 422
    assert c.post(f"/api/projects/{pid}/music",
                  json={"track": "calm.mp3"}).status_code == 200
    assert c.get(f"/api/projects/{pid}").json()["music"] == "calm.mp3"
    # empty track resets to automatic
    assert c.post(f"/api/projects/{pid}/music",
                  json={"track": ""}).json()["track"] == ""
    # a shipped bed is selectable and streamable, not just listed
    shipped = next((t for t in listed if t != "calm.mp3"), None)
    assert shipped, "no bundled music beds found in assets/music/"
    assert c.post(f"/api/projects/{pid}/music",
                  json={"track": shipped}).status_code == 200
    assert c.get("/api/music/file", params={"name": shipped}).status_code == 200


def test_mix_preview_defaults_to_an_installed_bed(client):
    """The old default `track=default.mp3` does not exist in the shipped set, so
    the mix preview 404'd on every fresh install even with beds present."""
    c, _ = client
    beds = c.get("/api/music").json()
    assert beds, "no music beds available at all"
    r = c.get("/api/mix/preview")
    # no TTS in CI can make this a 503; what matters is it is no longer a 404
    assert r.status_code != 404, r.text


def test_audio_settings_roundtrip_and_range(client):
    c, _ = client
    assert c.post("/api/settings/audio", json={"voice_volume": 70, "music_volume": 50}).status_code == 200
    assert c.get("/api/settings/audio").json() == {"voice_volume": 70, "music_volume": 50}
    # out of range values are clamped
    assert c.post("/api/settings/audio", json={"voice_volume": 150, "music_volume": -10}).status_code == 200
    r = c.get("/api/settings/audio").json()
    assert r["voice_volume"] == 100
    assert r["music_volume"] == 0


def test_produce_batch_queues_serially(client):
    c, db = client
    r = c.post("/api/produce",
               json={"topics": ["Alpha", "", "Beta", "Gamma"]}).json()
    assert r["ok"] is True and len(r["projects"]) == 3  # empty line skipped
    q = c.get("/api/queue").json()
    assert q["pending"] == r["projects"] and q["running"] == []
    topics = set()
    for p in r["projects"]:
        iid = db.get("projects", p)["idea_id"]
        topics.add(db.get("ideas", iid)["topic"])
    assert topics == {"Alpha", "Beta", "Gamma"}


def test_produce_single_uses_serial_queue(client):
    """Regression: single topics bypassed the queue — clicking "New video"
    during a batch ran two renders concurrently (OOM on 16GB machines)."""
    c, db = client
    r = c.post("/api/produce", json={"topic": "Solo"}).json()
    assert c.get("/api/queue").json()["pending"] == [r["project"]]


def test_produce_fails_fast_without_api_key(client):
    """409 up front, instead of a 200 that only dies later at RESEARCHING with
    a reason the dashboard never persists (projects has no error column)."""
    from core.settings import set_setting
    c, db = client
    set_setting("llm.api_key", "")
    r = c.post("/api/produce", json={"topic": "No key"})
    assert r.status_code == 409 and "AI model" in r.json()["detail"]
    assert db.all("projects") == []  # nothing created, nothing queued


def test_produce_fails_fast_without_stock_key(client):
    """Same reasoning for footage: stock_sources() drops any source with no key,
    and there is no AI video generator to fall back on — a keyless run only ever
    produces stills with a slow zoom."""
    from core.settings import set_setting
    c, db = client
    set_setting("image.pexels_key", "")
    r = c.post("/api/produce", json={"topic": "No footage key"})
    assert r.status_code == 409 and "stock footage" in r.json()["detail"]
    assert db.all("projects") == []


def test_worker_skips_cancelled_queued_project(client, monkeypatch):
    """A project cancelled while waiting in the queue must be dropped, not
    started (the run would instantly re-cancel itself off the stale flag)."""
    import threading
    import apps.api.server as srv
    c, db = client
    r = c.post("/api/produce", json={"topics": ["Doomed", "Alive"]}).json()
    db.update("projects", r["projects"][0], status="CANCELLED")
    ran = []
    monkeypatch.setattr(srv, "_run_sync", lambda pid: ran.append(pid))
    t = threading.Thread(target=srv._queue_worker_loop, daemon=True)
    t.start()
    srv._produce_queue.join()  # worker drained both entries
    assert ran == [r["projects"][1]]


def test_stage_idea_prefers_seeded_topic(client, monkeypatch):
    """Regression: stage_idea re-picked from the shared NEW pool (oldest
    score-100 tie wins) and overwrote the project's user topic."""
    from apps.orchestrator.pipeline import build_stages
    from core.jobs import State

    c, db = client
    decoy = db.insert("ideas", topic="old leftover", source="user", score=100,
                      status="NEW", created_at="2026-01-01T00:00:00")
    r = c.post("/api/produce", json={"topics": ["My real topic"]}).json()
    pid = r["projects"][0]
    seed_id = db.get("projects", pid)["idea_id"]
    assert seed_id != decoy
    stages = build_stages(pid)
    res = stages[State.IDEA](pid, State.IDEA)
    assert res.success is True
    assert db.get("projects", pid)["idea_id"] == seed_id
    assert db.get("ideas", seed_id)["status"] == "SELECTED"
    # unseeded project still takes the pool path
    iid = db.insert("ideas", topic="T")
    sid = db.insert("scripts", idea_id=iid)
    pid2 = db.insert("projects", script_id=sid)  # no idea_id yet
    res2 = build_stages(pid2)[State.IDEA](pid2, State.IDEA)
    assert res2.success and db.get("projects", pid2)["idea_id"] == decoy


def test_resume_uses_persisted_idea_not_newest(client, monkeypatch):
    """Regression: a resumed run skips stage_idea, and the resolver fell
    back to max(idea id) — another project's idea on a busy DB."""
    from apps.orchestrator.pipeline import build_stages
    from core.jobs import State

    c, db = client
    captured = {}
    monkeypatch.setattr("apps.research.research_agent.research_idea",
                        lambda db, iid: captured.update(iid=iid) or {"s": 1})
    mine = db.insert("ideas", topic="mine", source="user", score=100,
                     status="SELECTED", created_at="2026-01-01T00:00:00")
    db.insert("ideas", topic="decoy newest", created_at="2026-01-02T00:00:00")
    sid = db.insert("scripts", idea_id=mine)
    pid = db.insert("projects", script_id=sid, idea_id=mine, status="RESEARCHING")
    res = build_stages(pid)[State.RESEARCHING](pid, State.RESEARCHING)
    assert res.success and captured["iid"] == mine


def test_delete_queued_project_conflicts(client):
    c, db = client
    r = c.post("/api/produce", json={"topics": ["QueuedOne"]}).json()
    pid = r["projects"][0]
    assert c.delete(f"/api/projects/{pid}").status_code == 409


def test_sse_delivers_broadcast(client):
    """Regression: the endpoint's finally discarded the listener as soon as
    StreamingResponse was returned — no event ever reached any client."""
    import asyncio

    import apps.api.server as srv

    async def main():
        srv._MAIN_LOOP = asyncio.get_running_loop()  # as startup would
        resp = await srv.events()
        it = resp.body_iterator
        first = await asyncio.wait_for(it.__anext__(), 3)
        assert "connected" in first
        # stages broadcast from plain worker threads — same crossing here
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: srv.broadcast("voice_settings_changed",
                                        {"voice": "af_heart"}))
        second = await asyncio.wait_for(it.__anext__(), 3)
        assert "voice_settings_changed" in second
        await it.aclose()
        assert not srv._listeners  # cleanup happens on stream close

    asyncio.run(main())
