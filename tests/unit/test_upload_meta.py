"""Upload metadata overrides, daemon settings, subtitle download, sync rescore."""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv
import core.settings as settings_mod
from core.config import config
from core.database import Database


@pytest.fixture()
def client(monkeypatch, tmp_path):
    db = Database(config.db_path)
    monkeypatch.setattr(srv, "get_db", lambda: db)
    monkeypatch.setattr(srv, "_running", set())
    with TestClient(srv.app) as c:
        yield c, db


def _project(db, topic="t", status="APPROVAL", youtube_id=None):
    iid = db.insert("ideas", topic=topic)
    sid = db.insert("scripts", idea_id=iid)
    return db.insert("projects", script_id=sid, idea_id=iid, status=status,
                     youtube_id=youtube_id)


def test_approve_stores_title_and_description(client, monkeypatch, tmp_path):
    c, db = client
    from apps.uploader import youtube as yt
    monkeypatch.setattr(yt, "TOKEN_PATH", tmp_path / "missing.json")
    pid = _project(db)
    r = c.post(f"/api/projects/{pid}/approve",
               json={"title": "  My Custom Title  ",
                     "description": "Custom desc\nwith lines"})
    assert r.status_code == 409  # no token — but metadata already stored
    from core.settings import get_setting
    assert get_setting(f"project.{pid}.yt_title") == "My Custom Title"
    assert get_setting(f"project.{pid}.yt_desc") == "Custom desc\nwith lines"


def test_suggest_title_uses_llm_mock(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("apps.research.title_gen.generate_titles",
                        lambda t, h="": {
                            "titles": [{"title": f"{t}: The Truth", "score": 9}],
                            "description": f"About {t}", "hashtags": [],
                            "best": {"title": f"{t}: The Truth", "score": 9}})
    pid = _project(client[1], topic="Ghost Ships")
    r = c.post(f"/api/projects/{pid}/suggest-title")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Ghost Ships: The Truth"
    assert body["description"] == "About Ghost Ships"
    assert {"title": "Ghost Ships: The Truth", "score": 9, "why": ""} in body["titles"]


def test_daemon_settings_validation_and_roundtrip(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(srv, "_stop_daemon", lambda: None)
    monkeypatch.setattr(srv, "_start_daemon_if_enabled", lambda: False)
    assert c.post("/api/settings/daemon",
                  json={"enabled": True, "videos_per_day": 0}).status_code == 422
    assert c.post("/api/settings/daemon",
                  json={"enabled": True, "start_hour": 25}).status_code == 422
    assert c.post("/api/settings/daemon",
                  json={"enabled": False, "videos_per_day": 5,
                        "start_hour": 2, "end_hour": 6}).status_code == 200
    got = c.get("/api/settings/daemon").json()
    assert got == {"enabled": False, "running": False,
                   "videos_per_day": 5, "start_hour": 2, "end_hour": 6}


def test_subtitles_download(client, monkeypatch, tmp_path):
    c, db = client
    d = tmp_path / "content" / "projects" / "project-00001"
    d.mkdir(parents=True)
    assert c.get("/api/subtitles/1").status_code == 404  # nothing rendered yet
    (d / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n")
    r = c.get("/api/subtitles/1")
    assert r.status_code == 200 and "hi" in r.text


def test_promo_is_appended_once():
    """The channel promo lands after the caption and never duplicates."""
    from apps.uploader.youtube import (LEGACY_PROMOS, PROMO_MARK, refresh_promo,
                                       with_promo)

    out = with_promo("Body text")
    assert out.startswith("Body text")          # the summary keeps the top
    assert out.rstrip().endswith(PROMO_MARK)
    assert with_promo(out) == out  # idempotent
    assert PROMO_MARK in with_promo("")

    # a video carrying a superseded copy is rewritten, not left alone — and the
    # old block is moved off the top of the description
    for legacy in LEGACY_PROMOS:
        old = f"{legacy}\n\nBody text"
        refreshed = refresh_promo(old)
        assert legacy not in refreshed
        assert refreshed.startswith("Body text")
        assert refreshed.rstrip().endswith(PROMO_MARK)
        assert refresh_promo(refreshed) == refreshed  # idempotent
    # a description that never had a promo now gets one, at the end
    plain = refresh_promo("Body text")
    assert plain.startswith("Body text") and plain.rstrip().endswith(PROMO_MARK)


def test_apply_promo_edits_with_put_not_insert(monkeypatch):
    """apply_promo must PUT. POST /youtube/v3/videos is videos.insert, which
    creates a new empty public video and bills the 100/day upload bucket."""
    import requests

    from apps.uploader import youtube as y

    methods = []

    class Resp:
        status_code = 200
        text = '{"items": []}'

        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"snippet": {"title": "T", "description": "Body text",
                                           "tags": [], "categoryId": "27"}}]}

    monkeypatch.setattr(requests, "request",
                        lambda method, *a, **k: (methods.append(method), Resp())[1])
    monkeypatch.setattr(y, "_creds", lambda: {"client_id": "test-id"})
    monkeypatch.setattr(y, "_access_token", lambda c: "tok")

    assert y.apply_promo("abc123") == "updated"
    # the read is a GET; the description edit must be a PUT, never a POST
    assert "PUT" in methods, f"videos.update must use PUT, got {methods}"
    assert "POST" not in methods, f"POST is videos.insert, got {methods}"


def test_apply_promo_never_loses_the_existing_description(monkeypatch):
    """The owner's rule: only the promo is touched, the description stays.

    A snippet PUT replaces the whole snippet, so every field that was fetched
    must be sent back verbatim — and the body text must survive intact with
    the promo appended after it.
    """
    import requests

    from apps.uploader import youtube as y

    body = ("The deepest point on Earth hides a secret.\n\n"
            "Chapters:\n0:00 Intro\n1:20 The trench")
    sent = {}

    class Resp:
        status_code = 200
        text = '{"items": []}'

        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"snippet": {
                "title": "T", "description": body, "tags": ["a", "b"],
                "categoryId": "27", "defaultLanguage": "en"}}]}

    def capture(method, url, **kwargs):
        if method == "PUT":
            sent.update(kwargs.get("json") or {})
        return Resp()

    monkeypatch.setattr(requests, "request", capture)
    monkeypatch.setattr(y, "_creds", lambda: {"client_id": "test-id"})
    monkeypatch.setattr(y, "_access_token", lambda c: "tok")

    assert y.apply_promo("abc123") == "updated"
    snippet = sent["snippet"]
    assert snippet["description"].startswith(body)      # body untouched, first
    assert snippet["description"].rstrip().endswith(y.PROMO_MARK)
    assert snippet["title"] == "T"
    assert snippet["tags"] == ["a", "b"]
    assert snippet["categoryId"] == "27"
    assert snippet["defaultLanguage"] == "en"           # not silently dropped


def test_longform_description_gets_hashtags_and_keyword_tags(tmp_path, monkeypatch):
    """16:9 videos carry their own title/description, which used to skip the
    hashtag line AND leave the YouTube keyword-tags field empty — Shorts got
    both from the same LLM call, long-form got neither."""
    import apps.orchestrator.pipeline as pipe
    from apps.quality import quality_gate as qg
    from apps.research import title_gen as tg
    from apps.uploader import youtube as yt
    from core.database import Database
    from core.jobs import State

    db = Database(config.db_path)
    iid = db.insert("ideas", topic="6 Places on Earth No Human Can Legally Set Foot")
    sid = db.insert("scripts", idea_id=iid)
    pid = db.insert("projects", script_id=sid, idea_id=iid, status="UPLOADING",
                    aspect_ratio="16:9")

    vid_dir = tmp_path / "content" / "rendered" / f"project-{pid:05d}"
    vid_dir.mkdir(parents=True)
    (vid_dir / "final.mp4").write_bytes(b"x")

    settings_mod.set_setting(f"project.{pid}.yt_title", "My Long Title")
    settings_mod.set_setting(f"project.{pid}.yt_desc",
                             "My own summary.\n\nChapters:\n0:00 Intro")

    calls = []

    def fake_titles(t, h="", existing_titles=None):
        calls.append(existing_titles)
        return {"titles": [], "description": "llm", "best": None,
                "hashtags": ["DeepSea", "#Ocean"],
                "tags": ["deep sea", "ocean facts"]}

    monkeypatch.setattr(qg.QualityGate, "check_technical", lambda self: [])
    monkeypatch.setattr(tg, "generate_titles", fake_titles)

    sent = {}

    def fake_upload(video, title="", description="", tags=None,
                    privacy="unlisted", publish_at=""):
        sent.update(title=title, description=description, tags=tags)
        return "vid123"

    monkeypatch.setattr(yt, "upload_video", fake_upload)

    res = pipe.build_stages(pid)[State.UPLOADING](pid, State.UPLOADING)
    assert res.success, res.error
    desc = sent["description"]
    assert sent["title"] == "My Long Title"
    assert desc.startswith("My own summary.")                 # body untouched
    assert "#DeepSea #Ocean" in desc                          # hashtags added
    assert desc.index("#DeepSea") > desc.index("Chapters")    # appended, not prepended
    # keyword tags: the invisible search field, previously empty on 16:9
    assert sent["tags"] == ["deep sea", "ocean facts"]
    # one extra LLM call only, and it skips the channel-title lookup
    assert calls == [[]]


def test_tags_backfill_helpers_and_description_order():
    """The backfill must only ADD, and the promo must stay last.

    Pure logic, no API: the description assembly is where a mistake would
    silently mangle 155 live descriptions.
    """
    import importlib.util
    from pathlib import Path

    from apps.uploader.youtube import PROMO, _strip_superseded, with_promo

    path = Path(__file__).resolve().parents[2] / "scripts" / "tags_backfill.py"
    spec = importlib.util.spec_from_file_location("tags_backfill", path)
    tb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tb)

    # keyword tags: lowercase, no '#', deduped, capped
    assert tb.trim_tags(["Deep Sea", "deep sea", "#Ocean", "", "ocean"]) == \
        ["deep sea", "ocean"]
    assert tb.trim_tags(["x" * 600]) == []          # a single over-long tag is dropped
    assert sum(len(t) + 1 for t in tb.trim_tags(["a" * 100] * 20)) <= 500

    # hashtags: '#' added, case-insensitive dedupe
    assert tb.hashtag_line(["Ocean", "#ocean", "DeepSea"]) == "#Ocean #DeepSea"

    # assembly: body preserved, hashtags inserted, promo re-appended LAST
    live = "My own summary.\n\nChapters:\n0:00 Intro\n\n" + PROMO
    body = _strip_superseded(live)
    assert PROMO not in body and body.startswith("My own summary.")
    new = with_promo(f"{body}\n\n#Ocean #DeepSea")
    assert new.startswith("My own summary.")
    assert "#Ocean #DeepSea" in new
    assert new.rstrip().endswith(PROMO.strip().splitlines()[-1])
    assert new.index("#Ocean") < new.index("DeepSea") < new.index("ZenPilot Pro")
    # re-running is a no-op, so the backfill is safe to repeat
    assert with_promo(new) == new
    assert tb.HASHTAG_RE.search("#Ocean #DeepSea")
    assert not tb.HASHTAG_RE.search("a # b and # no")


def test_uploader_public_surface_is_intact():
    """The uploader's public API must survive a refactor.

    A wholesale rewrite of youtube.py once dropped finish_auth/oauth_flow and
    silently changed upload_video's signature from
    (video, title, description, tags, privacy, publish_at) to (project_id).
    Nothing failed in CI — the API endpoints and the pipeline import these at
    call time, so the break only surfaced on a live upload. Lock the surface.
    """
    import inspect

    from apps.uploader import youtube as y

    for name in ("with_promo", "refresh_promo", "apply_promo", "upload_video",
                 "save_secret", "auth_url", "finish_auth", "oauth_flow",
                 "set_thumbnail", "list_channel_videos", "list_playlists",
                 "create_playlist", "add_to_playlist", "_api", "_creds"):
        assert callable(getattr(y, name, None)), f"youtube.{name} is missing"

    sig = inspect.signature(y.upload_video)
    params = list(sig.parameters)
    assert params[:2] == ["video", "title"], (
        f"upload_video's first params changed: {params}. The pipeline calls it "
        f"as upload_video(video, title=..., description=..., tags=..., "
        f"privacy=..., publish_at=...).")
    for name in ("description", "tags", "privacy", "publish_at"):
        assert name in params, f"upload_video lost the {name} parameter"

    # the token file the auth flow writes, not a stray token.json
    assert y.TOKEN_PATH.name == "youtube_token.json"


def test_api_honours_explicit_delete(monkeypatch):
    """_api(method="DELETE") must issue DELETE, not fall back to GET.

    With no body the old wrapper always used requests.get, so a delete would
    silently become a read and report success while removing nothing.
    """
    import requests

    from apps.uploader import youtube as y

    calls = []

    class Resp:
        status_code = 204
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {}

    monkeypatch.setattr(requests, "request",
                        lambda method, url, **k: (calls.append((method, url)), Resp())[1])
    monkeypatch.setattr(y, "_creds", lambda: {"client_id": "test-id"})
    monkeypatch.setattr(y, "_access_token", lambda c: "tok")

    assert y._api("videos", {"id": "abc123"}, method="DELETE") == {}
    assert calls and calls[0][0] == "DELETE", f"expected DELETE, got {calls}"


def test_upload_held_does_nothing_on_import(monkeypatch):
    """Importing upload_held must NOT publish anything.

    Every statement used to sit at module scope, so importing the module for any
    reason walked the project table, moved every project at APPROVAL to
    UPLOADING and published it — project 296 went public that way. The work now
    lives in main() behind an if __name__ guard.
    """
    import importlib.util
    from pathlib import Path

    import apps.orchestrator.pipeline as pl

    repo = Path(__file__).resolve().parents[2]
    ran = []
    monkeypatch.setattr(pl, "run_pipeline",
                        lambda pid, **k: ran.append(pid))
    monkeypatch.setattr(pl, "get_db", lambda: (_ for _ in ()).throw(
        AssertionError("import must not touch the database")))

    spec = importlib.util.spec_from_file_location(
        "upload_held_probe", repo / "scripts" / "upload_held.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert ran == [], f"importing the module ran the pipeline for {ran}"
    assert mod.HELD_STATES == ("APPROVAL", "UPLOADING")


def test_analytics_sync_rescores(client, monkeypatch):
    c, db = client
    monkeypatch.setattr("apps.analytics.collector.fetch_all_channel_stats",
                        lambda: [{"youtube_id": "abc123", "title": "T",
                                  "views": 10, "likes": 1, "comments": 0}])
    monkeypatch.setattr("apps.analytics.feedback.rescore_pending_ideas",
                        lambda db: 3)
    _project(db, status="COMPLETE", youtube_id="abc123")
    r = c.post("/api/analytics/sync").json()
    assert r["updated"] == 1 and r["rescored"] == 3
