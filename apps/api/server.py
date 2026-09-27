"""FastAPI server (spec: Browser UI → FastAPI → Job System → Workers).

REST + SSE. Serves the built Next.js frontend from web/out (static export).
Run: avf serve  →  http://0.0.0.0:8600
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.config import config, data_dir
from core.database import Database
from core.logging import get_logger
from apps.editor.music import list_beds, resolve_bed
from apps.orchestrator.pipeline import get_db, run_pipeline, write_manifest

# The bridge is explicitly user-triggered in the extension popup. Only browser
# extension origins are allowed; normal websites cannot call this endpoint.

log = get_logger("api")

app = FastAPI(title="AI Video Factory", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|moz-extension)://[a-z0-9-]+$",
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"])


class SecurityHeadersMiddleware:
    """Add baseline security headers to every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def wrapped_send(event):
            if event["type"] == "http.response.start":
                headers = event.get("headers") or []
                extra = {
                    b"X-Content-Type-Options": b"nosniff",
                    b"X-Frame-Options": b"DENY",
                    b"Referrer-Policy": b"strict-origin-when-cross-origin",
                    b"Permissions-Policy": b"accelerometer=(), camera=(), geolocation=(), microphone=()",
                }
                # Avoid clobbering explicitly set headers
                lower_names = {h[0].lower() for h in headers}
                for k, v in extra.items():
                    if k.lower() not in lower_names:
                        headers.append((k, v))
                event["headers"] = headers
            await send(event)
        await self.app(scope, receive, wrapped_send)


app.add_middleware(SecurityHeadersMiddleware)

# ── auth gate ────────────────────────────────────────────────────
# ponytail: auth is opt-in while this single-user AVF instance is being
# tested; set AVF_AUTH_ENABLED=1 before exposing it beyond the private domain.
from apps.api import auth as _auth  # noqa: E402
_AUTH_ENABLED = os.getenv("AVF_AUTH_ENABLED", "0") == "1"
# Set by packaging/desktop.py. Gates POST /api/quit, which must never be
# reachable on a server install where it would stop someone else's service.
_DESKTOP = os.getenv("AVF_DESKTOP", "0") == "1"


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    path = request.url.path
    if (_AUTH_ENABLED and path.startswith("/api")
            and not path.startswith(_auth.OPEN)
            and not _auth.valid_session(request)):
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=302)  # browser → login page
        return JSONResponse({"detail": "not authenticated"}, status_code=401)
    return await call_next(request)

# ── event bus for SSE ───────────────────────────────────────────
_listeners: set[asyncio.Queue] = set()


def broadcast(event: str, data: dict) -> None:
    """Push an event to all connected UI clients (called from stages).

    Stages run in plain worker threads — hop through the event loop so the
    asyncio.Queue wakeups stay thread-safe (direct put_nowait can leave a
    waiting SSE reader asleep)."""
    msg = json.dumps({"event": event, **data})

    def _put(q: "asyncio.Queue[str]") -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    loop = _MAIN_LOOP
    for q in list(_listeners):
        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(_put, q)
            except RuntimeError:
                pass  # loop shutting down mid-broadcast
        else:
            _put(q)


# bridge core event bus → SSE so the UI sees every stage transition live
from core import events as _bus
_bus.subscribe("project_state", lambda **d: broadcast("project_state", d))

_MAIN_LOOP: "asyncio.AbstractEventLoop | None" = None

@app.on_event("startup")
async def _startup():
    """Auto-produce keeps running across server restarts when enabled."""
    global _MAIN_LOOP
    _MAIN_LOOP = asyncio.get_running_loop()
    try:
        _start_daemon_if_enabled()
    except Exception:
        log.exception("daemon autostart failed")
    try:
        _ensure_queue_worker()
    except Exception:
        log.exception("queue worker start failed")
    try:
        # crontab is derived state: if the DB was changed outside the settings
        # UI (restore/import), cron would stay stale and a slot silently
        # stops firing. Re-derive on every boot.
        from apps.uploader.schedule import sync_cron
        sync_cron()
    except Exception:
        log.exception("cron sync on startup failed")


@app.get("/api/health")
def health():
    """Liveness probe for the desktop launcher — polls until the server answers.

    Deliberately unauthenticated (it sits in auth.OPEN) and free of DB and file
    work: the launcher calls it before the user has logged in or configured
    anything.
    """
    return {"ok": True, "service": "avf", "version": app.version,
            "auth_required": _AUTH_ENABLED, "desktop": _DESKTOP}


@app.post("/api/quit")
def quit_app():
    """Stop the desktop app. Refused unless the launcher started this process.

    The response has to reach the browser before the process dies, so the exit
    is deferred by a beat rather than done inline.
    """
    if not _DESKTOP:
        raise HTTPException(403, "quit is only available in the desktop app")
    import threading

    def _die():
        import time
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True}


@app.get("/api/events")
async def events():
    """SSE stream: job/state updates in real time."""
    async def gen():
        # register INSIDE the generator: a finally around the endpoint body
        # would drop the listener the moment StreamingResponse is returned,
        # before any byte streams (this silently killed all live updates)
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        _listeners.add(q)
        try:
            yield ": connected\n\n"
            while True:
                msg = await q.get()
                yield f"data: {msg}\n\n"
        finally:
            _listeners.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ── serializers ─────────────────────────────────────────────────
def _project_row(db: Database, p: dict) -> dict:
    idea = db.get("ideas", p["idea_id"]) if p.get("idea_id") else None
    video = data_dir() / "content" / "rendered" / f"project-{p['id']:05d}" / "final.mp4"
    return {**{k: p.get(k) for k in
               ("id", "status", "idea_id", "script_id", "youtube_id",
                "views", "likes", "comments", "archived", "created_at",
                "aspect_ratio", "storyboard_only")},
            "topic": (idea or {}).get("topic"),
            "has_video": video.exists(),
            # render mtime → player URL cache-buster so every browser
            # refetches after a rebuild
            "video_v": int(video.stat().st_mtime) if video.exists() else None}


@app.get("/api/projects")
def list_projects(include_archived: int = 0):
    db = get_db()
    rows = [_project_row(db, p) for p in db.all("projects")
            if include_archived or not p.get("archived")]
    return sorted(rows, key=lambda r: -r["id"])


@app.post("/api/projects/{pid}/archive")
def archive_project(pid: int, body: dict = None):
    """Archive (hide from Dashboard/Gallery) or restore a project."""
    db = get_db()
    if not db.get("projects", pid):
        raise HTTPException(404, "not found")
    want = bool(body.get("archived")) if body else True
    db.update("projects", pid, archived=int(want))
    return {"ok": True, "archived": want}


@app.get("/api/projects/{pid}")
def project_detail(pid: int):
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "project not found")
    out = _project_row(db, p)
    script = db.get("scripts", p["script_id"]) if p.get("script_id") else None
    if script:
        out["script"] = {"hook": script.get("hook"), "body": script.get("body"),
                         "cta": script.get("cta")}
    from core.settings import get_setting
    out["music"] = get_setting(f"project.{pid}.music") or ""
    # Ordered by scene number, not by row id. db.all defaults to `ORDER BY id
    # DESC`, which served the storyboard backwards: Scene 7 at the top, Scene 1
    # at the bottom, on every project.
    scenes = db.all("scenes", "script_id=? ORDER BY scene_number", (p["script_id"],)) \
        if p.get("script_id") else []
    # imported clips live on disk, not in the DB — surface them so the UI
    # can show which scene slots are already filled (and the source filename)
    from core.filesystem import project_dir
    from core.progress import get_progress
    pdir = project_dir(pid)
    out["progress"] = get_progress(pid)
    def _asset_name(n: int) -> str | None:
        f = pdir / "assets" / f"scene-{n:02d}.mp4"
        if not f.exists():
            return None
        nf = f.with_suffix(".mp4.name")
        return nf.read_text(encoding="utf-8").strip() if nf.exists() else f.name
    out["scenes"] = [dict(s, has_video=(pdir / "assets" / f"scene-{s['scene_number']:02d}.mp4").exists(),
                          asset_name=_asset_name(s["scene_number"]))
                     for s in scenes]
    return out


@app.post("/api/projects/{pid}/scenes/{num}/asset")
async def upload_scene_asset(pid: int, num: int, file: UploadFile):
    """Import an externally generated clip (e.g. Gemini/Veo) for one scene."""
    db = get_db()
    if not db.get("projects", pid):
        raise HTTPException(404, "project not found")
    name = (file.filename or "").lower()
    if not name.endswith((".mp4", ".webm", ".mov")):
        raise HTTPException(422, "file must be .mp4, .webm or .mov")
    from core.filesystem import project_dir
    dest = project_dir(pid) / "assets" / f"scene-{num:02d}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # stream to disk with a cap — buffering the whole upload in memory can
    # OOM the box on a big clip
    MAX_UPLOAD_BYTES = 512 * 2**20
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "clip larger than 512MB")
                out.write(chunk)
    finally:
        await file.close()
    if not written:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "empty file")
    dest.with_suffix(".mp4.name").write_text(file.filename or dest.name, encoding="utf-8")
    broadcast("project_state", {"project_id": pid})
    return {"ok": True, "path": str(dest), "bytes": written}


@app.delete("/api/projects/{pid}/scenes/{num}/asset")
def delete_scene_asset(pid: int, num: int):
    """Remove an imported clip so the pipeline regenerates it."""
    from core.filesystem import project_dir
    dest = project_dir(pid) / "assets" / f"scene-{num:02d}.mp4"
    if not dest.exists():
        raise HTTPException(404, "no uploaded asset for this scene")
    dest.unlink()
    dest.with_suffix(".pexels-id").unlink(missing_ok=True)
    dest.with_suffix(".mp4.name").unlink(missing_ok=True)
    broadcast("project_state", {"project_id": pid})
    return {"ok": True}


class Decision(BaseModel):
    reason: str = ""


_running: set[int] = set()


def _run_sync(pid: int):
    """Run one project's pipeline to completion on the calling thread.
    Returns the end State, or None if the pipeline raised."""
    _running.add(pid)
    try:
        end = run_pipeline(pid)
        broadcast("pipeline_done", {"project": pid, "state": end.value})
        return end
    except Exception as e:
        # persist the failure — otherwise the row freezes mid-stage and
        # the UI pulses forever on a dead pipeline
        log.exception("pipeline %s failed", pid)
        get_db().update("projects", pid, status="FAILED")
        broadcast("pipeline_error", {"project": pid, "error": str(e)})
        return None
    finally:
        _running.discard(pid)


def _run_in_background(pid: int):
    """Run pipeline off the event loop; broadcast state changes."""
    import threading
    threading.Thread(target=_run_sync, args=(pid,), daemon=True).start()


# serial production queue — batch topics are rendered ONE AT A TIME so a
# burst of LLM/render jobs never stacks up on the machine
import queue as _queue_mod
_produce_queue: "_queue_mod.Queue[int]" = _queue_mod.Queue()
_QUEUE_THREAD = None


def _queued_project_dead(pid: int) -> bool:
    """Cancelled while waiting in the queue → drop it instead of letting the
    worker start a run that instantly re-cancels itself (stale flag)."""
    return (get_db().get("projects", pid) or {}).get("status") == "CANCELLED"


def _queue_worker_loop():
    """Drain the serial produce queue forever (module-level so tests can run
    their own thread against a patched queue)."""
    while True:
        pid = _produce_queue.get()
        try:
            if not _queued_project_dead(pid):
                # manual creations ALWAYS stop at APPROVAL — auto-publish is
                # exclusive to the daily schedule cron (user directive 2026-08-26)
                _run_sync(pid)
        finally:
            _produce_queue.task_done()


def _ensure_queue_worker():
    global _QUEUE_THREAD
    if _QUEUE_THREAD is None or not _QUEUE_THREAD.is_alive():
        _QUEUE_THREAD = threading.Thread(target=_queue_worker_loop, daemon=True,
                                         name="produce-queue")
        _QUEUE_THREAD.start()


class ApproveRequest(BaseModel):
    publish_at: str | None = None  # RFC3339 UTC — private now, public then
    title: str | None = None       # override the auto-generated YouTube title
    description: str | None = None
    privacy: str | None = None     # "public" | "unlisted" | "private" (default public)
    playlist: str | None = None    # playlist name — created on the channel if missing


@app.post("/api/projects/{pid}/approve")
def approve(pid: int, body: ApproveRequest | None = None):
    from core.jobs import State
    db = get_db()
    p = db.get("projects", pid)
    if not p or p["status"] != "APPROVAL":
        raise HTTPException(409, f"project is {p['status'] if p else 'missing'}, not APPROVAL")
    from core.settings import set_setting
    if body and body.publish_at:
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(body.publish_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "publish_at must be an ISO/RFC3339 datetime")
        if dt <= datetime.now(timezone.utc):
            raise HTTPException(422, "publish_at must be in the future")
        set_setting(f"project.{pid}.publish_at", body.publish_at)
    if body and body.title and body.title.strip():
        set_setting(f"project.{pid}.yt_title", body.title.strip())
    if body and body.description and body.description.strip():
        set_setting(f"project.{pid}.yt_desc", body.description.strip())
    if body and body.privacy:
        if body.privacy not in ("public", "unlisted", "private"):
            raise HTTPException(422, "privacy must be public|unlisted|private")
        set_setting(f"project.{pid}.privacy", body.privacy)
    if body and body.playlist is not None:
        set_setting(f"project.{pid}.playlist", body.playlist.strip())
    # fail fast with a clear message instead of burning the project to FAILED
    from apps.uploader.youtube import TOKEN_PATH
    if not TOKEN_PATH.exists():
        raise HTTPException(409, "YouTube not connected — run 'avf youtube auth' first, "
                                 "or download the video from /api/video/%d" % pid)
    if pid in _running or pid in _produce_queue.queue:
        raise HTTPException(409, "pipeline already running for this project")
    db.update("projects", pid, status="UPLOADING")
    _run_in_background(pid)
    return {"ok": True, "state": "UPLOADING"}


@app.post("/api/projects/{pid}/reject")
def reject(pid: int, body: Decision | None = None):
    """Discard the video outright — no note, no second step."""
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    if pid in _running or pid in _produce_queue.queue:
        raise HTTPException(409, "pipeline already running for this project")
    db.update("projects", pid, status="FAILED")
    return {"ok": True, "state": "FAILED"}


def _clear_generated(pid: int) -> None:
    """A revision regenerates everything downstream of the script; clips, audio
    and renders from the rejected version must not leak into the new one."""
    import shutil
    from core.filesystem import project_dir
    pdir = project_dir(pid)
    for name in ("assets", "audio", "subtitles", "rendered"):
        shutil.rmtree(pdir / name, ignore_errors=True)


@app.post("/api/projects/{pid}/revise")
def revise(pid: int, body: Decision):
    """Regenerate the script, storyboard and video with the user's note wired
    into the prompts — a revision loop, not a re-render of the same thing."""
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    note = body.reason.strip()
    if not note:
        raise HTTPException(422, "Revise needs a note saying what to change — "
                                 "use Reject to discard the video.")
    if pid in _running or pid in _produce_queue.queue:
        raise HTTPException(409, "pipeline already running for this project")
    from core.settings import set_setting
    set_setting(f"project.{pid}.revision_note", note)
    _clear_generated(pid)
    db.update("projects", pid, status="SCRIPTING")
    _run_in_background(pid)
    return {"ok": True, "state": "SCRIPTING"}


class ProduceRequest(BaseModel):
    topic: str | None = None
    topics: list[str] | None = None  # batch → serial production queue
    # omitted/"9:16" = a Short, exactly as before. "16:9" tags the project
    # landscape and sets a duration budget, which routes the script generator
    # to the long-form countdown path. One format per batch, by design.
    aspect_ratio: str = "9:16"
    target_minutes: float | None = None


def _apply_format(pid: int, ratio: str, minutes: float | None) -> None:
    """Tag a freshly created project as long-form and set its duration budget."""
    from apps.orchestrator.pipeline import get_db
    from core.settings import set_setting
    get_db().update("projects", pid, aspect_ratio="16:9")
    set_setting(f"project.{pid}.target_duration",
                str((minutes or 6.0) * 60))
    set_setting(f"project.{pid}.max_scene_duration", "5")


@app.post("/api/produce")
def produce(body: ProduceRequest | None = None):
    """Start new project(s). topics[] are queued and rendered one at a time."""
    from apps.orchestrator.pipeline import create_project
    from core.settings import get_setting
    # fail fast: with no API key the pipeline only dies later, at RESEARCHING,
    # and the reason never lands in the DB (projects has no error column) — the
    # user sees a bare "Failed" badge after a reload.
    if not get_setting("llm.api_key"):
        raise HTTPException(409, "AI model not configured — open /setup "
                                 "(or Settings → AI model) and add a base URL, "
                                 "model and API key first")
    # same reasoning for footage: stock_sources() drops any source without a
    # key, and there is no AI video generator to fall back on — a keyless run
    # only ever produces stills with a slow zoom, which is not the product.
    from providers.stock import stock_sources
    if not stock_sources():
        raise HTTPException(409, "No stock footage API key — open /setup "
                                 "(or Settings → Media) and add a free Pexels "
                                 "or Pixabay key first")
    ratio = (body.aspect_ratio if body else "9:16") or "9:16"
    if ratio not in ("16:9", "9:16"):
        raise HTTPException(422, "aspect_ratio must be 16:9 or 9:16")
    minutes = body.target_minutes if body else None
    if body and body.topics:
        tops = [t.strip() for t in body.topics[:20] if t.strip()]
        pids = [create_project(t) for t in tops]
        for pid in pids:
            if ratio == "16:9":
                _apply_format(pid, ratio, minutes)
            _produce_queue.put(pid)
        _ensure_queue_worker()
        return {"ok": True, "projects": pids}
    # single topics go through the same serial queue as batches — a direct
    # start would run two pipelines concurrently during a batch (OOM on 16GB)
    pid = create_project(body.topic if body else None)
    if ratio == "16:9":
        _apply_format(pid, ratio, minutes)
    _produce_queue.put(pid)
    _ensure_queue_worker()
    return {"ok": True, "project": pid}


@app.get("/api/queue")
def queue_status():
    """Honest view of the serial production queue."""
    return {"running": sorted(_running), "pending": list(_produce_queue.queue)}


# ── storyboard-only projects ──────────────────────────────────
class StoryboardRequest(BaseModel):
    topic: str
    scene_count: int = 5
    max_scene_duration: float = 15.0
    aspect_ratio: str = "9:16"


@app.post("/api/storyboard")
def create_storyboard(body: StoryboardRequest):
    """Create a storyboard-only project: generate idea → research → script →
    storyboard, then pause at STORYBOARDED. User uploads per-scene videos later."""
    from apps.orchestrator.pipeline import create_project, get_db as _get_db, build_stages
    from core.jobs import State, Orchestrator
    from core.progress import set_progress, clear_progress
    import threading

    if body.aspect_ratio not in ("16:9", "9:16"):
        raise HTTPException(422, "aspect_ratio must be 16:9 or 9:16")
    # long-form (16:9) needs far more scenes than a 60s Short
    max_scenes = 80 if body.aspect_ratio == "16:9" else 10
    if body.scene_count < 1 or body.scene_count > max_scenes:
        raise HTTPException(422, f"scene_count must be 1..{max_scenes}")
    if body.max_scene_duration < 1:
        raise HTTPException(422, "max_scene_duration must be at least 1")
    if len(body.topic.strip()) < 2 or len(body.topic.strip()) > 500:
        raise HTTPException(422, "topic must be 2..500 characters")

    pid = create_project(body.topic.strip())
    db = _get_db()
    db.update("projects", pid, storyboard_only=1, aspect_ratio=body.aspect_ratio)
    # persist max_scene_duration so storyboard_gen can read it
    from core.settings import set_setting
    set_setting(f"project.{pid}.max_scene_duration", str(body.max_scene_duration))
    set_setting(f"project.{pid}.scene_count", str(body.scene_count))

    def _run():
        _running.add(pid)
        try:
            # use the pipeline's run but the STORYBOARDED handler
            # will be the last stage we care about — after that we stop
            from apps.orchestrator.pipeline import run_pipeline
            stages = build_stages(pid)
            pct = {"IDEA": 5, "RESEARCHING": 15, "SCRIPTING": 30, "STORYBOARDING": 40}
            wrapped = {}
            for st, fn in stages.items():
                if st in (State.IDEA, State.RESEARCHING, State.SCRIPTING,
                          State.STORYBOARDING):
                    def handler(pid, s, _fn=fn):
                        set_progress(pid, s.value, pct.get(s.value, 50))
                        return _fn(pid, s)
                    wrapped[st] = handler
            orch = Orchestrator(db, wrapped)
            end = orch.run(pid)
            if end not in (State.FAILED, State.CANCELLED):
                db.update("projects", pid, status="STORYBOARDED")
                from core import events
                events.emit("project_state", project_id=pid, state="STORYBOARDED")
        except Exception as e:
            log.error("storyboard pipeline failed for %s: %s", pid, e)
            try:
                db.update("projects", pid, status="FAILED")
            except Exception:
                pass
        finally:
            clear_progress(pid)
            _running.discard(pid)
            broadcast("project_state", {"project_id": pid})

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "project": pid}


@app.post("/api/projects/{pid}/render-storyboard")
def render_storyboard(pid: int):
    """Render a storyboard-only project. All scene videos must be uploaded first."""
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "project not found")
    if p.get("status") != "STORYBOARDED":
        raise HTTPException(422, "project must be in STORYBOARDED state")
    if p.get("storyboard_only") != 1:
        raise HTTPException(422, "not a storyboard-only project")

    # verify all scene videos are uploaded
    from core.filesystem import project_dir
    pdir = project_dir(pid)
    # Same ordering as the detail endpoint: this list is shown to the user, so
    # "missing scene(s): [7, 6, 1]" was as confusing as the reversed storyboard.
    scenes = db.all("scenes", "script_id=? ORDER BY scene_number", (p["script_id"],)) \
        if p.get("script_id") else []
    missing = []
    for sc in scenes:
        vpath = pdir / "assets" / f"scene-{sc['scene_number']:02d}.mp4"
        if not vpath.exists():
            missing.append(sc["scene_number"])
    if missing:
        raise HTTPException(422, f"missing video for scene(s): {missing}")

    # run the remaining pipeline stages (EDITING → RENDERED → QUALITY_CHECK → APPROVAL → ...)
    _run_in_background(pid)
    return {"ok": True, "state": "EDITING"}


@app.get("/api/music")
def list_music():
    """Background tracks available: the user's music folder + the shipped beds."""
    return list_beds()


@app.get("/api/music/file")
def music_file(name: str):
    """Stream a BGM track for the Settings preview player."""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(422, "bad name")
    p = resolve_bed(name)
    if not p:
        raise HTTPException(404, "no such track")
    return FileResponse(p, media_type="audio/mpeg" if p.suffix == ".mp3" else "audio/wav")


@app.get("/api/mix/preview")
def mix_preview(track: str = "", voice_volume: int = 80, music_volume: int = 40):
    """VO sample + BGM mixed at user volume levels — hear the balance before rendering."""
    if "/" in track or "\\" in track or ".." in track:
        raise HTTPException(422, "bad name")
    # default to whatever bed is installed instead of a hardcoded name: the
    # shipped set has no default.mp3, so `track=default.mp3` 404'd on a fresh
    # install even though the picker showed beds.
    track = track or next(iter(list_beds()), "")
    src = resolve_bed(track)
    if not src:
        raise HTTPException(
            404, f"no music bed '{track}' — pick another bed or add an .mp3 to "
                 f"the music folder shown in Settings → Audio")
    from providers.tts.kokoro import KokoroTTS
    v = KokoroTTS().voice
    vo = data_dir() / "content" / "cache" / f"voice-preview-{v}.wav"
    if not vo.exists():
        vo.parent.mkdir(parents=True, exist_ok=True)
        try:
            KokoroTTS(voice=v).synth(
                "The world's most expensive car cost twenty eight million dollars. "
                "Here is what makes it worth that price.", vo)
        except Exception as e:
            raise HTTPException(503, f"TTS unavailable: {str(e)[:150]}")
    tag = f"{track.rsplit('.', 1)[0]}-{voice_volume}-{music_volume}"
    out = data_dir() / "content" / "cache" / f"mix-preview-{tag}.wav"
    if not out.exists():
        from apps.editor.music import mix_audio
        try:
            mix_audio(vo, src, out, voice_volume=voice_volume, music_volume=music_volume)
        except Exception as e:
            raise HTTPException(500, str(e)[:200])
    return FileResponse(out, media_type="audio/wav", filename=f"mix-{tag}.wav")


@app.get("/api/tts/preview")
def tts_preview(voice: str = "", text: str = ""):
    """Short voice sample for the Settings voice picker (~3s, cached)."""
    from providers.tts.kokoro import KokoroTTS, language_for_voice, resolve_auto_voice, voice_ids
    from providers.tts.piper import VOICES as PIPER_VOICES
    if voice and voice not in voice_ids():
        raise HTTPException(422, f"unknown voice '{voice}'")
    # each language hears its own sentence: an Indonesian voice must preview as
    # Indonesian, or the picker cannot show what the accent is actually like
    samples = {
        "en": "The world's most expensive car cost twenty eight million dollars. "
              "Here is what makes it worth that price.",
        "id": "Mobil termahal di dunia harganya dua puluh delapan juta dolar. "
              "Inilah yang membuat harganya sebesar itu.",
        "de": "Das teuerste Auto der Welt kostete achtundzwanzig Millionen Dollar. "
              "Das ist der Grund für diesen Preis.",
        "es": "El coche más caro del mundo costó veintiocho millones de dólares. "
              "Esto es lo que justifica su precio.",
        "fr": "La voiture la plus chère du monde coûtait vingt-huit millions de "
              "dollars. Voici ce qui justifie ce prix.",
        "it": "L'auto più costosa al mondo costava ventotto milioni di dollari. "
              "Ecco cosa giustifica quel prezzo.",
    }
    lang = language_for_voice(voice) if voice else "en"
    lang = (lang or "en")[:2]
    sample = text.strip()[:200] or samples.get(lang, samples["en"])
    v = voice or KokoroTTS().voice
    # an auto voice previews as the concrete voice for the sample's language
    if v in ("auto-female", "auto-male"):
        v = resolve_auto_voice(v, sample)
    if v.startswith("piper:") and v not in PIPER_VOICES:
        raise HTTPException(422, f"unknown voice '{voice}'")
    cache = data_dir() / "content" / "cache" / f"voice-preview-{v}.wav"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        try:
            # the sample is written in the voice's own language, so you hear
            # exactly the accent you are choosing
            KokoroTTS(voice=v).synth(sample, cache)
        except Exception as e:
            raise HTTPException(503, f"TTS unavailable: {str(e)[:150]}")
    return FileResponse(cache, media_type="audio/wav", filename=f"{v}.wav")


class MusicRequest(BaseModel):
    track: str = ""  # empty → back to automatic (default.mp3 / none)


@app.post("/api/projects/{pid}/music")
def set_project_music(pid: int, body: MusicRequest):
    db = get_db()
    if not db.get("projects", pid):
        raise HTTPException(404, "project not found")
    track = body.track.strip()
    if track and track not in list_music():
        raise HTTPException(422, "unknown track")
    from core.settings import set_setting
    set_setting(f"project.{pid}.music", track)
    return {"ok": True, "track": track}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    """Remove a project permanently: cascaded DB rows + files on disk."""
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    if pid in _running:
        raise HTTPException(409, "pipeline is running — cancel it first")
    if pid in _produce_queue.queue:
        raise HTTPException(409, "project is queued — cancel it first")
    from core.cleanup import purge_project
    freed = purge_project(db, pid)
    broadcast("project_state", {"project_id": pid, "state": "DELETED"})
    return {"ok": True, "freed_bytes": freed}


class BulkDeleteRequest(BaseModel):
    ids: list[int]


@app.delete("/api/projects/{pid}/video")
def delete_project_video(pid: int):
    """Gallery delete: drop the rendered video, keep the project record."""
    db = get_db()
    if not db.get("projects", pid):
        raise HTTPException(404, "not found")
    if pid in _running or pid in _produce_queue.queue:
        raise HTTPException(409, "pipeline is running — cancel it first")
    from core.cleanup import free_project_files
    freed = free_project_files(pid, render_only=True)
    broadcast("project_state", {"project_id": pid, "state": "VIDEO_DELETED"})
    return {"ok": True, "freed_bytes": freed}


@app.post("/api/projects/bulk-delete-videos")
def bulk_delete_project_videos(req: BulkDeleteRequest):
    """Gallery bulk delete: drop rendered videos, keep every project."""
    db = get_db()
    from core.cleanup import free_project_files
    deleted = 0
    total_freed = 0
    for pid in req.ids:
        if not db.get("projects", pid) or pid in _running or pid in _produce_queue.queue:
            continue
        total_freed += free_project_files(pid, render_only=True)
        deleted += 1
        broadcast("project_state", {"project_id": pid, "state": "VIDEO_DELETED"})
    return {"ok": True, "deleted": deleted, "freed_bytes": total_freed}


@app.post("/api/projects/bulk-delete")
def bulk_delete_projects(req: BulkDeleteRequest):
    """Permanently delete multiple projects."""
    db = get_db()
    from core.cleanup import purge_project
    deleted = 0
    total_freed = 0
    for pid in req.ids:
        if pid in _running or pid in _produce_queue.queue:
            continue
        freed = purge_project(db, pid)
        if freed >= 0:
            deleted += 1
            total_freed += freed
            broadcast("project_state", {"project_id": pid, "state": "DELETED"})
    return {"ok": True, "deleted": deleted, "freed_bytes": total_freed}


@app.post("/api/projects/{pid}/clone")
def clone_project(pid: int):
    """Duplicate idea/script/scenes + project files into a ready-to-render copy."""
    import shutil
    from core.filesystem import project_dir
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    if pid in _running:
        raise HTTPException(409, "pipeline is running — cancel it first")
    idea = db.get("ideas", p["idea_id"]) if p.get("idea_id") else None
    new_iid = db.insert(
        "ideas", channel_id=(idea or {}).get("channel_id"),
        topic=f"{(idea or {}).get('topic') or f'Project {pid}'} (clone)",
        source="clone", status="NEW")
    new_sid = None
    if p.get("script_id"):
        s = db.get("scripts", p["script_id"])
        new_sid = db.insert(
            "scripts", idea_id=new_iid, version=s.get("version") or 1,
            hook=s.get("hook"), body=s.get("body"), cta=s.get("cta"),
            duration=s.get("duration"), status=s.get("status") or "DRAFT")
        for sc in db.all("scenes", "script_id=?", (p["script_id"],)):
            db.insert("scenes", script_id=new_sid,
                      scene_number=sc["scene_number"], duration=sc["duration"],
                      narration=sc["narration"], visual_prompt=sc["visual_prompt"],
                      motion_prompt=sc["motion_prompt"],
                      text_overlay=sc["text_overlay"],
                      asset_type=sc.get("asset_type") or "image",
                      asset_path=sc.get("asset_path"), status="ASSET_READY")
    # EDITING needs assets/ + audio/ on disk; copying the whole dir keeps
    # uploaded clips (they win over providers) and per-scene narration
    new_pid = db.insert("projects", script_id=new_sid, idea_id=new_iid,
                        status="EDITING")
    src, dst = project_dir(pid), project_dir(new_pid)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    _run_in_background(new_pid)
    return {"ok": True, "project": new_pid}


_THUMB = "thumb.jpg"


def _thumb_path(pid: int):
    from core.config import config as _cfg
    return _cfg.root / "content" / "rendered" / f"project-{pid:05d}" / _THUMB


@app.post("/api/projects/{pid}/thumbnail")
def make_thumbnail(pid: int):
    """1280x720 YouTube thumbnail: mid-frame of final.mp4 + title overlay."""
    from apps.editor.thumbnail import make_thumbnail
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    final = data_dir() / "content" / "rendered" / f"project-{pid:05d}" / "final.mp4"
    if not final.exists():
        raise HTTPException(409, "no rendered video yet")
    idea = db.get("ideas", p["idea_id"]) if p.get("idea_id") else None
    topic = (idea or {}).get("topic") or f"Project {pid}"
    try:
        make_thumbnail(final, _thumb_path(pid), topic)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    broadcast("project_state", {"project_id": pid})
    return {"ok": True, "url": f"/api/thumbnail/{pid}?v={int(_thumb_path(pid).stat().st_mtime)}"}


@app.get("/api/thumbnail/{pid}")
def thumbnail_file(pid: int):
    f = _thumb_path(pid)
    if not f.exists():
        raise HTTPException(404, "no thumbnail yet — generate one first")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.post("/api/projects/{pid}/suggest-title")
def suggest_title(pid: int):
    """LLM title/description suggestion for the approval card."""
    from apps.research.title_gen import generate_titles
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    idea = db.get("ideas", p["idea_id"]) if p.get("idea_id") else None
    topic = (idea or {}).get("topic") or f"AI Video Project {pid}"
    hook = (idea or {}).get("hook", "")
    try:
        meta = generate_titles(topic, hook)
    except Exception as e:
        raise HTTPException(502, f"title generation failed: {str(e)[:150]}")
    return {"topic": topic,
            "title": (meta.get("best") or {}).get("title") or topic,
            "description": meta.get("description") or hook,
            "titles": [{"title": t["title"], "score": t.get("score", 0),
                        "why": t.get("why", "")}
                       for t in meta.get("titles", []) if t.get("title")]}


@app.get("/api/subtitles/{pid}")
def download_subtitles(pid: int):
    """Serve the rendered subtitle file (.srt or .ass) for upload to YouTube."""
    from core.filesystem import project_dir
    d = project_dir(pid)
    for name in ("subtitles.srt", "subtitles.ass"):
        f = d / name
        if f.exists():
            return FileResponse(f, filename=f.name,
                                headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "no subtitle file — render the video first")


@app.post("/api/projects/{pid}/cancel")
def cancel_project(pid: int):
    """Stop a pipeline: cooperatively if running, directly otherwise."""
    from core.jobs import request_cancel, State
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    if p["status"] in ("COMPLETE", "FAILED", "CANCELLED"):
        raise HTTPException(409, f"project already {p['status']}")
    if pid in _running:
        request_cancel(pid)
        return {"ok": True, "mode": "stopping"}
    # queued rows may be picked up by the worker any instant — raise the
    # cooperative flag too so a run that starts mid-call still stops at its
    # next stage boundary (stale flags are discarded on first sight)
    request_cancel(pid)
    # no live thread (stale row / server restart) → cancel in place
    db.update("projects", pid, status=State.CANCELLED.value)
    broadcast("project_state", {"project_id": pid, "state": "CANCELLED"})
    return {"ok": True, "mode": "cancelled"}


@app.post("/api/projects/{pid}/retry")
def retry_project(pid: int):
    """Re-run a failed/cancelled project from its last completed milestone.

    Also serves as "rebuild video": a finished project with scene assets on
    disk re-enters at EDITING (assemble clips + subs, no LLM/TTS reruns).
    """
    db = get_db()
    p = db.get("projects", pid)
    if not p:
        raise HTTPException(404, "not found")
    if pid in _running:
        raise HTTPException(409, "pipeline already running for this project")
    from core.filesystem import project_dir
    pdir = project_dir(pid)
    scenes = db.all("scenes", "script_id=?", (p["script_id"],)) \
        if p.get("script_id") else []
    assets_ready = bool(scenes) and all(
        (pdir / "assets" / f"scene-{s['scene_number']:02d}").with_suffix(".mp4").exists()
        or (pdir / "assets" / f"scene-{s['scene_number']:02d}").with_suffix(".png").exists()
        for s in scenes)
    # voice + subs live from the previous render — all three required so a
    # rebuild really is assembly-only (no LLM/TTS/Whisper reruns)
    rendered = data_dir() / "content" / "rendered" / f"project-{pid:05d}"
    assembly = assets_ready and (rendered / "narration.wav").exists() \
        and ((pdir / "subtitles.srt").exists() or (pdir / "subtitles.ass").exists())
    # a completed project may only be rebuilt as a pure clip swap
    ok_status = p["status"] in ("FAILED", "CANCELLED", "APPROVAL", "RENDERED")
    if not ok_status and not (p["status"] == "COMPLETE" and assembly):
        raise HTTPException(409, f"project is {p['status']}, nothing to retry")
    if assembly:
        st = "EDITING"
    elif (rendered / "final.mp4").exists():
        st = "QUALITY_CHECK"  # video exists → re-check, don't regenerate
    elif p["script_id"]:
        st = "STORYBOARDING"
    elif p["idea_id"] and db.all("research", "idea_id=?", (p["idea_id"],)):
        st = "SCRIPTING"
    elif p["idea_id"]:
        st = "RESEARCHING"
    else:
        st = "IDEA"
    db.update("projects", pid, status=st)
    broadcast("project_state", {"project_id": pid, "state": st})
    from core.jobs import clear_cancel
    clear_cancel(pid)  # retrying a cancelled project must not eat the old flag
    _run_in_background(pid)
    return {"ok": True, "state": st}


@app.get("/api/analytics")
def analytics():
    db = get_db()
    seen_yt: set[str] = set()
    out = []
    # Active projects with youtube_id (DB source of truth)
    # Sort by created_at DESC so newest videos appear first (restored projects
    # carry high IDs from renumbering but old dates)
    for p in sorted(db.all("projects"),
                    key=lambda r: (r.get("created_at") or "", r["id"]),
                    reverse=True):
        if p.get("youtube_id"):
            seen_yt.add(p["youtube_id"])
            out.append({**_project_row(db, p),
                        "url": f"https://youtube.com/watch?v={p['youtube_id']}"})
    # Analytics table rows (YouTube-only + orphaned videos)
    for a in db.all("analytics", "youtube_id != ''"):
        yid = a["youtube_id"]
        if yid in seen_yt:
            continue
        seen_yt.add(yid)
        out.append({
            "id": None, "topic": a.get("title", "") or f"YT: {yid}",
            "youtube_id": yid,
            "views": a.get("views", 0), "likes": a.get("likes", 0),
            "comments": a.get("comments", 0),
            "url": f"https://youtube.com/watch?v={yid}",
        })
    return out


@app.post("/api/analytics/sync")
def analytics_sync():
    from apps.analytics.collector import fetch_all_channel_stats
    from datetime import datetime, timezone
    db = get_db()
    updated = errors = 0
    now = datetime.now(timezone.utc).isoformat()
    try:
        all_stats = fetch_all_channel_stats()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    # Index DB projects by youtube_id
    proj_by_yt = {p["youtube_id"]: p for p in db.all("projects") if p.get("youtube_id")}
    for vs in all_stats:
        yid = vs["youtube_id"]
        proj = proj_by_yt.get(yid)
        try:
            if proj:
                # Update projects table
                db.update("projects", proj["id"],
                          views=vs["views"], likes=vs["likes"],
                          comments=vs["comments"])
            # Upsert into analytics table
            rows = db.all("analytics", "youtube_id=?", (yid,))
            if rows:
                db.update("analytics", rows[0]["id"],
                          title=vs["title"], views=vs["views"],
                          likes=vs["likes"], comments=vs["comments"],
                          collected_at=now)
            else:
                db.insert("analytics", video_id=0, youtube_id=yid, title=vs["title"],
                          views=vs["views"], likes=vs["likes"],
                          comments=vs["comments"], collected_at=now)
            updated += 1
        except Exception:
            errors += 1
    broadcast("analytics_synced", {"updated": updated})
    rescored = 0
    try:  # view performance re-ranks pending ideas (same as `avf analytics sync`)
        from apps.analytics.feedback import rescore_pending_ideas
        rescored = rescore_pending_ideas(db)
    except Exception:
        pass
    return {"ok": True, "updated": updated, "rescored": rescored,
            "errors": errors}


def _win_mem_status():
    """ctypes binding for MEMORYSTATUSEX — 64 bytes on every platform.

    DWORD is spelled c_uint32 rather than c_ulong on purpose: c_ulong is 8 bytes
    off Windows, which would size the struct wrong, and keeping it fixed lets
    the size be asserted from a machine that cannot call the function.
    """
    import ctypes

    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64)]

    return MemStatus


def _mem_stats_mb() -> dict:
    """Total/available RAM in MB — Linux, macOS and Windows, stdlib only."""
    import os
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        total = page * os.sysconf("SC_PHYS_PAGES")
        avail = page * os.sysconf("SC_AVPHYS_PAGES")
        return {"ram_total_mb": total // 2**20, "ram_free_mb": avail // 2**20}
    except (ValueError, OSError, AttributeError):
        pass  # macOS raises ValueError here; Windows has no os.sysconf at all
    if os.name == "nt":
        # The System page divides these two, so an empty dict renders "NaN GB"
        # rather than degrading quietly — worth the native call.
        import ctypes

        st = _win_mem_status()()
        st.dwLength = ctypes.sizeof(st)  # the call fails without this
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return {"ram_total_mb": st.ullTotalPhys // 2**20,
                    "ram_free_mb": st.ullAvailPhys // 2**20}
        return {}
    try:  # macOS fallback
        import subprocess
        total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
        vm = subprocess.check_output(["vm_stat"]).decode()
        free = sum(int(line.split(":")[1].strip().rstrip(".")) for line in vm.splitlines()
                   if line.startswith(("Pages free", "Pages inactive", "Pages speculative")))
        return {"ram_total_mb": total // 2**20,
                "ram_free_mb": free * 16384 // 2**20}  # vm_stat pages are 16KB on arm64
    except Exception:
        return {}


@app.get("/api/system")
def system():
    import shutil
    mem = _mem_stats_mb()
    disk = shutil.disk_usage(data_dir())
    models = []
    try:
        from core.model_manager import discover_models, verify_model
        models = [{**m, "available": verify_model(m["name"])}
                  for m in discover_models()]
    except Exception:
        pass
    return {**mem, "disk_free_gb": round(disk.free / 2**30, 1), "models": models}


@app.get("/api/video/{pid}")
def video_file(pid: int):
    path = data_dir() / "content" / "rendered" / f"project-{pid:05d}" / "final.mp4"
    if not path.exists():
        raise HTTPException(404, "video not rendered yet")
    # no-cache: revalidate each load so a rebuilt video is never stale
    return FileResponse(path, media_type="video/mp4",
                        headers={"Cache-Control": "no-cache"})


# ── YouTube settings (client secret + OAuth connect) ────────────
class YouTubeSecret(BaseModel):
    content: str


class YouTubeCode(BaseModel):
    code: str


@app.get("/api/settings/youtube")
def youtube_status():
    from apps.uploader.youtube import CLIENT_SECRET, TOKEN_PATH
    info: dict = {"connected": TOKEN_PATH.exists(), "has_secret": CLIENT_SECRET.exists()}
    if not TOKEN_PATH.exists():
        return info
    # Live check: refresh the token and read the channel identity.
    try:
        from apps.uploader.youtube import _creds, _access_token
        import requests as _rq
        access = _access_token(_creds())
        r = _rq.get("https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "snippet", "mine": "true"},
                    headers={"Authorization": f"Bearer {access}"}, timeout=15)
        items = r.json().get("items", [])
        if items:
            info.update(alive=True,
                        channel_title=items[0]["snippet"]["title"],
                        channel_id=items[0]["id"])
        else:
            info.update(alive=False, error="token works but no channel found")
    except Exception as e:
        msg = str(e)
        if "invalid_grant" in msg or "401" in msg or "400" in msg:
            info.update(alive=False,
                        error="Token expired/revoked — reconnect required "
                              "(Testing-mode tokens die every 7 days)")
        else:
            info.update(alive=False, error=msg[:200])
    return info


@app.delete("/api/settings/youtube")
def youtube_disconnect():
    """Forget stored token (client secret kept). Next connect = fresh login."""
    from apps.uploader.youtube import TOKEN_PATH
    TOKEN_PATH.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/youtube/playlists")
def yt_playlists():
    from apps.uploader.youtube import list_playlists
    try:
        return {"playlists": list_playlists()}
    except Exception as e:
        raise HTTPException(502, f"playlist list failed: {e}")


class PlaylistCreate(BaseModel):
    title: str


@app.post("/api/youtube/playlists")
def yt_playlist_create(body: PlaylistCreate):
    if not body.title.strip():
        raise HTTPException(422, "title required")
    from apps.uploader.youtube import create_playlist
    try:
        return {"id": create_playlist(body.title.strip())}
    except Exception as e:
        raise HTTPException(502, f"playlist create failed: {e}")


# ── daily upload schedule ────────────────────────────────────────────
class ScheduleBody(BaseModel):
    id: str | None = None
    enabled: bool = False
    time: str = "09:00"
    videos_per_run: int = 1
    privacy: str = "public"
    format: str = "9:16"
    target_minutes: float = 6.0
    days: dict[str, dict[str, str]] = {}


@app.get("/api/settings/schedule")
def get_upload_schedule():
    from apps.uploader.schedule import get_schedule
    return get_schedule()

@app.post("/api/settings/schedule")
def set_upload_schedule(body: ScheduleBody):
    from apps.uploader.schedule import save_schedule
    save_schedule(body.model_dump())
    return get_upload_schedule()

@app.get("/api/settings/schedules")
def get_upload_schedules():
    from apps.uploader.schedule import cron_available, get_schedules
    # cron_available rides along on the read path only: it describes the host,
    # not the schedule, and the UI keeps it in its own state so a save response
    # cannot blank it.
    return [{**s, "cron_available": cron_available()} for s in get_schedules()]

@app.post("/api/settings/schedules")
def set_upload_schedules(body: list[ScheduleBody]):
    from apps.uploader.schedule import save_schedules
    return save_schedules([b.model_dump() for b in body])

@app.post("/api/settings/schedules/add")
def add_upload_schedule():
    from apps.uploader.schedule import get_schedules, add_schedule
    scheds = get_schedules()
    return add_schedule(scheds)

@app.delete("/api/settings/schedules/{sched_id}")
def delete_upload_schedule(sched_id: str):
    from apps.uploader.schedule import get_schedules, delete_schedule
    scheds = get_schedules()
    return delete_schedule(scheds, sched_id)


@app.post("/api/settings/youtube/secret")
def youtube_secret(body: YouTubeSecret):
    from apps.uploader.youtube import save_secret
    try:
        save_secret(body.content)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.post("/api/settings/youtube/start")
def youtube_start():
    from apps.uploader.youtube import auth_url
    try:
        return {"url": auth_url()}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(409, str(e))


@app.post("/api/settings/youtube/finish")
def youtube_finish(body: YouTubeCode):
    from apps.uploader.youtube import finish_auth
    try:
        finish_auth(body.code)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"token exchange failed: {e}")
    return {"ok": True}


# ── LLM provider settings (BYOK API only) ───────────────────────
class LLMSettings(BaseModel):
    api_base_url: str | None = None
    api_key: str | None = None         # empty = keep stored key
    api_model: str | None = None
    api_fallback_models: str | None = None  # comma-separated models tried after api_model fails


class ImageSettings(BaseModel):
    provider: str                      # auto|pexels
    pexels_key: str | None = None      # empty = keep stored key
    pixabay_key: str | None = None     # empty = keep stored key
    stock_sources: str | None = None   # comma list: pexels,pixabay
    asset_mode: str | None = None      # auto|image|stock (scene motion source)


@app.get("/api/settings/image")
def get_image_settings():
    from core.settings import get_setting
    from providers.image import PROVIDERS, image_provider_setting
    from providers.stock import stock_enabled
    pkey = get_setting("image.pexels_key") or ""
    bkey = get_setting("image.pixabay_key") or ""
    return {"provider": image_provider_setting(), "providers": list(PROVIDERS),
            "asset_mode": get_setting("video.asset_mode") or "auto",
            "stock_active": stock_enabled(),
            "stock_sources": get_setting("video.stock_sources") or "pexels",
            "has_pexels_key": bool(pkey),
            "pexels_key_hint": ("…" + pkey[-4:]) if len(pkey) >= 4 else None,
            "has_pixabay_key": bool(bkey),
            "pixabay_key_hint": ("…" + bkey[-4:]) if len(bkey) >= 4 else None}


@app.post("/api/settings/image")
def save_image_settings(body: ImageSettings):
    from core.settings import get_setting, set_setting
    from providers.image import PROVIDERS
    if body.provider not in PROVIDERS:
        raise HTTPException(422, f"unknown image provider — expected one of "
                                 f"{', '.join(PROVIDERS)}")
    if body.provider == "pexels" and not (body.pexels_key or get_setting("image.pexels_key")):
        raise HTTPException(422, "pexels_key required — none stored yet")
    set_setting("image.provider", body.provider)
    if body.pexels_key:
        set_setting("image.pexels_key", body.pexels_key)
    if body.pixabay_key:
        set_setting("image.pixabay_key", body.pixabay_key)
    if body.stock_sources is not None:
        want = [s.strip().lower() for s in body.stock_sources.split(",") if s.strip()]
        bad = [s for s in want if s not in ("pexels", "pixabay")]
        if bad:
            raise HTTPException(422, f"unknown stock source(s): {', '.join(bad)}")
        if not want:
            raise HTTPException(422, "pick at least one stock source")
        set_setting("video.stock_sources", ",".join(want))
    if body.asset_mode:
        if body.asset_mode not in ("auto", "image", "stock"):
            raise HTTPException(422, "asset_mode must be auto, image or stock")
        set_setting("video.asset_mode", body.asset_mode)
    broadcast("image_settings_changed", {"provider": body.provider})
    return {"ok": True}


class SubtitleSettings(BaseModel):
    preset: str


@app.get("/api/settings/subtitle")
def get_subtitle_settings():
    from apps.editor.subtitles import PRESETS, subtitle_preset
    return {"preset": subtitle_preset(),
            "presets": [{"id": k, "label": v["label"]}
                        for k, v in PRESETS.items()]}


@app.post("/api/settings/subtitle")
def save_subtitle_settings(body: SubtitleSettings):
    from core.settings import set_setting
    from apps.editor.subtitles import PRESETS
    if body.preset not in PRESETS:
        raise HTTPException(422, "unknown subtitle preset")
    set_setting("subtitle.preset", body.preset)
    broadcast("subtitle_settings_changed", {"preset": body.preset})
    return {"ok": True}


@app.get("/api/settings/subtitle/preview")
def subtitle_preview(preset: str):
    """One real frame — or, for word-by-word presets, a real animation — of
    `preset`, rendered by libass. Not a mock-up.

    Burned with the same `subtitles=` filter the encoder uses, so it cannot
    drift from what the render actually produces.

    Cached on disk and keyed on the mtime of the module that defines the
    presets: rendering is a full ffmpeg run (6 per page view), and a preset edit
    has to invalidate it or the user judges the new style by the old picture.
    """
    from apps.editor import subtitles as S
    if preset not in S.PRESETS:
        raise HTTPException(422, "unknown subtitle preset")
    stamp = int(Path(S.__file__).stat().st_mtime)
    # Word/karaoke presets animate — a still shows only the first word — so they
    # come back as a GIF and the cache key carries the extension.
    animated = S.PRESETS[preset].get("mode") in ("word", "karaoke")
    ext = "gif" if animated else "png"
    cache = data_dir() / "cache" / "subtitle-preview" / f"{preset}-{stamp}.{ext}"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        S.render_preview(preset, cache.with_suffix(f".tmp.{ext}"))
        cache.with_suffix(f".tmp.{ext}").replace(cache)
        # One stamp per edit: older ones would otherwise accumulate forever.
        for old in cache.parent.glob(f"{preset}-*"):
            if old != cache and old.is_file():
                old.unlink(missing_ok=True)
    return FileResponse(cache, media_type="image/gif" if animated else "image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


class VoiceSettings(BaseModel):
    voice: str

@app.get("/api/settings/voice")
def get_voice_settings():
    from core.settings import get_setting
    from providers.tts.kokoro import (KokoroTTS, LEGACY_VOICE_MAP,
                                      voice_options)
    stored = LEGACY_VOICE_MAP.get(get_setting("tts.voice") or "",
                                  get_setting("tts.voice"))
    return {"voice": stored or KokoroTTS().voice,
            "voices": voice_options()}

@app.post("/api/settings/voice")
def save_voice_settings(body: VoiceSettings):
    from core.settings import set_setting
    from providers.tts.kokoro import voice_ids
    if body.voice not in voice_ids():
        raise HTTPException(422, "unknown narration voice")
    set_setting("tts.voice", body.voice)
    broadcast("voice_settings_changed", {"voice": body.voice})
    return {"ok": True}


# ── Audio (voice + music volumes) ────────────────────────────────
class AudioSettings(BaseModel):
    voice_volume: int | None = None
    music_volume: int | None = None


@app.get("/api/settings/audio")
def get_audio_settings():
    from core.settings import get_setting
    from apps.editor.audio_levels import get_volumes
    voice, music = get_volumes(get_setting)
    return {"voice_volume": voice, "music_volume": music}


@app.post("/api/settings/audio")
def save_audio_settings(body: AudioSettings):
    from core.settings import set_setting
    vv = max(0, min(100, int(body.voice_volume if body.voice_volume is not None else 80)))
    mv = max(0, min(100, int(body.music_volume if body.music_volume is not None else 40)))
    set_setting("audio.voice_volume", str(vv))
    set_setting("audio.music_volume", str(mv))
    return {"ok": True}


# ── Daemon (automatic production) ───────────────────────────────
_DAEMON_STOP = threading.Event()
_DAEMON_GEN = 0          # bump to retire an old daemon thread mid-cycle
_daemon_thread: threading.Thread | None = None


def _daemon_params():
    from core.settings import get_setting
    return {"videos_per_day": int(get_setting("daemon.videos_per_day") or 3),
            "start_hour": int(get_setting("daemon.start_hour") or 1),
            "end_hour": int(get_setting("daemon.end_hour") or 7)}


def _start_daemon_if_enabled():
    global _DAEMON_GEN, _daemon_thread
    from core.settings import get_setting
    if get_setting("daemon.enabled") != "1":
        return False
    if _daemon_thread and _daemon_thread.is_alive():
        return True
    _DAEMON_GEN += 1
    gen = _DAEMON_GEN
    _DAEMON_STOP.clear()
    params = _daemon_params()

    def _run():
        from apps.orchestrator.daemon import run_daemon
        run_daemon(interval=300, target=5, stop=_DAEMON_STOP.is_set,
                   retired=lambda: _DAEMON_GEN != gen, **params)

    _daemon_thread = threading.Thread(target=_run, name="avf-daemon", daemon=True)
    _daemon_thread.start()
    log.info("daemon started (%s)", params)
    return True


def _stop_daemon():
    global _daemon_thread
    _DAEMON_STOP.set()
    th = _daemon_thread
    if th and th.is_alive():
        th.join(timeout=6)  # worst case: mid-cycle finishes its current tick
    _daemon_thread = None


class DaemonSettings(BaseModel):
    enabled: bool
    videos_per_day: int = 3
    start_hour: int = 1
    end_hour: int = 7

@app.get("/api/settings/daemon")
def get_daemon_settings():
    from core.settings import get_setting
    return {"enabled": get_setting("daemon.enabled") == "1",
            "running": bool(_daemon_thread and _daemon_thread.is_alive()),
            **_daemon_params()}

@app.post("/api/settings/daemon")
def save_daemon_settings(body: DaemonSettings):
    from core.settings import set_setting
    if not 1 <= body.videos_per_day <= 20:
        raise HTTPException(422, "videos_per_day must be 1-20")
    if not (0 <= body.start_hour <= 23 and 0 <= body.end_hour <= 23):
        raise HTTPException(422, "hours must be 0-23")
    set_setting("daemon.enabled", "1" if body.enabled else "0")
    set_setting("daemon.videos_per_day", str(body.videos_per_day))
    set_setting("daemon.start_hour", str(body.start_hour))
    set_setting("daemon.end_hour", str(body.end_hour))
    _stop_daemon()          # restart so new hours/count take effect
    running = _start_daemon_if_enabled() if body.enabled else False
    broadcast("daemon_settings_changed",
              {"enabled": body.enabled, "running": running})
    return {"ok": True, "running": running}


@app.get("/api/settings/llm")
def get_llm_settings():
    from core.settings import get_setting
    key = get_setting("llm.api_key") or ""
    return {"api_base_url": get_setting("llm.api_base_url") or "",
            "api_model": get_setting("llm.api_model") or "",
            "api_fallback_models": get_setting("llm.api_fallback_models") or "",
            "has_api_key": bool(key),
            "api_key_hint": ("…" + key[-4:]) if len(key) >= 4 else None}


@app.post("/api/settings/llm")
def save_llm_settings(body: LLMSettings):
    from core.settings import get_setting, set_setting
    from providers.llm import reset_llm_cache
    if not (body.api_base_url and body.api_model):
        raise HTTPException(422, "api_base_url and api_model are required")
    if not body.api_key and not get_setting("llm.api_key"):
        raise HTTPException(422, "api_key required — none stored yet")
    set_setting("llm.api_base_url", body.api_base_url)
    set_setting("llm.api_model", body.api_model)
    if body.api_fallback_models is not None:
        set_setting("llm.api_fallback_models", body.api_fallback_models)
    if body.api_key:
        set_setting("llm.api_key", body.api_key)
    reset_llm_cache()
    broadcast("llm_settings_changed", {"model": body.api_model})
    return {"ok": True}


@app.post("/api/llm/test")
def test_llm(body: LLMSettings):
    """One-shot generation with the given (or stored) credentials."""
    from core.settings import get_setting
    try:
        from providers.llm.openai_compat import OpenAICompatProvider
        base = body.api_base_url or get_setting("llm.api_base_url")
        key = body.api_key or get_setting("llm.api_key") or ""
        model = body.api_model or get_setting("llm.api_model") or ""
        if not (base and key and model):
            raise HTTPException(422, "base URL, model and API key required")
        prov = OpenAICompatProvider(base, key, model)
        reply = prov.generate("Reply with exactly one word: pong",
                              model=model, max_tokens=2000, temperature=0.0)
        return {"ok": True, "model": model or "", "reply": reply.strip()[:80]}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ── watermark settings + live preview ───────────────────────────
class WatermarkSettings(BaseModel):
    enabled: bool
    x: float            # 0..1, left edge
    y: float            # 0..1, top edge
    scale: float        # width fraction of frame (0.02..0.8)
    opacity: float      # 0..1
    ratio: str = "9:16"  # "16:9" | "9:16"


@app.get("/api/settings/watermark")
def get_wm_settings():
    from apps.editor.watermark import get_watermark_settings, DEFAULTS, RATIOS
    from core.database import Database
    db = Database(config.db_path)
    out = {}
    for ratio in RATIOS:
        s = get_watermark_settings(db, ratio)
        wm = data_dir() / s["path"] if s["path"] else None
        out[ratio] = {**s, "exists": bool(wm and wm.exists())}
    return {"defaults": DEFAULTS, "settings": out}


@app.post("/api/settings/watermark")
def save_wm_settings(body: WatermarkSettings):
    from core.settings import set_setting
    from apps.editor.watermark import RATIOS
    if body.ratio not in RATIOS:
        raise HTTPException(422, "ratio must be 16:9 or 9:16")
    if not (0 <= body.x <= 1 and 0 <= body.y <= 1):
        raise HTTPException(422, "x/y must be 0..1")
    if not (0.02 <= body.scale <= 0.8):
        raise HTTPException(422, "scale must be 0.02..0.8")
    if not (0 <= body.opacity <= 1):
        raise HTTPException(422, "opacity must be 0..1")
    rkey = body.ratio
    set_setting(f"watermark.{rkey}.enabled", "1" if body.enabled else "0")
    for k in ("x", "y", "scale", "opacity"):
        set_setting(f"watermark.{rkey}.{k}", str(getattr(body, k)))
    return {"ok": True}


@app.get("/api/watermark/preview.png")
def wm_preview(ratio: str = "9:16"):
    """Sample frame with the watermark composited at current settings —
    the Settings UI shows this image under a draggable handle."""
    import io
    from PIL import Image, ImageDraw
    from apps.editor.watermark import get_watermark_settings, RATIOS
    from core.database import Database
    if ratio not in RATIOS:
        ratio = "9:16"
    db = Database(config.db_path)
    s = get_watermark_settings(db, ratio)
    W, H = (480, 270) if ratio == "16:9" else (270, 480)
    img = Image.new("RGB", (W, H), (22, 27, 34))
    d = ImageDraw.Draw(img)
    for i in range(6):
        y = H * i // 6
        d.line([0, y, W, y], fill=(30, 36, 44))
        x = W * i // 6
        d.line([x, 0, x, H], fill=(30, 36, 44))
    wm_path = data_dir() / s["path"] if s["path"] else None
    if s["enabled"] and wm_path and wm_path.exists():
        wm = Image.open(wm_path).convert("RGBA")
        tw = max(16, round(W * s["scale"]))
        wm = wm.resize((tw, round(wm.height * tw / wm.width)))
        alpha = wm.split()[3].point(lambda a: int(a * s["opacity"]))
        wm.putalpha(alpha)
        px = round(s["x"] * W)
        py = round(s["y"] * H)
        img.paste(wm, (px, py), wm)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/watermark/file")
def wm_file(ratio: str = "9:16"):
    """Raw watermark image for the client-side live preview."""
    from apps.editor.watermark import get_watermark_settings, RATIOS
    from core.database import Database
    if ratio not in RATIOS:
        ratio = "9:16"
    db = Database(config.db_path)
    s = get_watermark_settings(db, ratio)
    p = data_dir() / s["path"] if s["path"] else None
    if not s["enabled"] or not p or not p.exists():
        raise HTTPException(404, "no watermark")
    return FileResponse(p, media_type="image/png")


@app.post("/api/watermark/upload")
async def upload_watermark(file: UploadFile):
    """Replace the watermark image (PNG with transparency recommended)."""
    name = (file.filename or "").lower()
    if not name.endswith((".png", ".webp")):
        raise HTTPException(422, "watermark must be .png or .webp")
    from core.settings import set_setting
    dest = data_dir() / "content" / "branding" / "watermark.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    MAX_WM_BYTES = 5 * 2**20
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                written += len(chunk)
                if written > MAX_WM_BYTES:
                    raise HTTPException(413, "watermark too large (max 5 MB)")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    # normalize to PNG RGBA so ffmpeg/PIL always composite it correctly
    from PIL import Image
    img = Image.open(dest).convert("RGBA")
    if max(img.size) > 2000:
        img.thumbnail((2000, 2000))
    img.save(dest, "PNG")
    set_setting("watermark.path", "content/branding/watermark.png")
    return {"ok": True, "size": [img.width, img.height],
            "bytes": dest.stat().st_size}


# ── auth endpoints ──────────────────────────────────────────────
class LoginBody(BaseModel):
    password: str


@app.get("/api/auth/status")
def auth_status():
    return {"has_password": _auth.has_password()}


@app.post("/api/auth/login")
def auth_login(body: LoginBody, request: Request):
    if not _auth.has_password():
        # bootstrap: first login ever sets the password
        try:
            _auth.set_password(body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        log.warning("initial admin password set via first login")
    elif not _auth.verify_password(body.password):
        raise HTTPException(401, "wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(_auth.COOKIE, _auth.make_token(),
                    max_age=_auth._MAX_AGE, httponly=True,
                    samesite="lax",
                    secure=request.url.scheme == "https")
    return resp


@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_auth.COOKIE)
    return resp


_LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AVF — Login</title>
<style>
/* Standalone by necessity: this page is served before the app boots, so it
   cannot read globals.css. The values are copied from that file's token block —
   keep them in step. Deliberately no web font: the app is offline-first, and a
   Google Fonts <link> made the login screen the one page that needed a CDN. */
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#EFF8F7;color:#10312F;display:grid;place-items:center;min-height:100vh;margin:0}
form{background:#FFFFFF;padding:32px;border-radius:16px;border:1px solid #CDE7E4;border-left:3px solid #3CC4BD;width:310px;box-shadow:0 4px 20px rgba(43,168,162,0.10)}
.brand-header{display:flex;align-items:center;gap:12px;margin-bottom:20px}
h1{font-size:1.15rem;font-weight:800;margin:0;letter-spacing:0.04em;text-transform:uppercase;color:#1E8C86}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:12px;border:1px solid #CDE7E4;background:#FFF8E7;color:#10312F;font-size:14px;outline:none;transition:border-color 150ms,box-shadow 150ms}
input:focus{border-color:#2BA8A2;box-shadow:0 0 0 4px rgba(43,168,162,0.15)}
button{width:100%;margin-top:14px;padding:11px;border:0;border-radius:999px;background:linear-gradient(180deg,#FFE47A,#FFD23F);color:#10312F;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 4px 20px rgba(255,210,63,0.40);transition:background 150ms}
button:hover{background:linear-gradient(180deg,#FFD23F,#E6B800)}
.err{color:#C0392B;font-size:13px;margin-top:12px;min-height:1em;text-align:center}
</style></head><body>
<form onsubmit="return go(event)">
<div class="brand-header">
  <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
    <rect x="2" y="4" width="20" height="16" rx="4" fill="#2BA8A2"/>
    <path d="M10 8.5L16 12L10 15.5V8.5Z" fill="#10312F"/>
  </svg>
  <h1>AVF Console</h1>
</div>
<input id=pw type=password placeholder="Password" autofocus required aria-label="Password">
<button>Login</button>
<div class=err id=e role=alert></div>
</form>
<script>
async function go(ev){ev.preventDefault();const r=await fetch('/api/auth/login',
{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({password:document.getElementById('pw').value})});
if(r.ok){location.href='/'}else{document.getElementById('e').textContent=
r.status===401?'Wrong password':'Error '+r.status}return false}
</script></body></html>"""


@app.get("/login")
def login_page():
    return PlainTextResponse(_LOGIN_HTML, media_type="text/html")


# ── static frontend (Next.js export) ────────────────────────────
WEB_OUT = Path(__file__).resolve().parents[2] / "web" / "out"
if WEB_OUT.exists():
    app.mount("/_next", StaticFiles(directory=WEB_OUT / "_next"), name="next")

    @app.get("/{full_path:path}")
    def spa(full_path: str, request: Request):
        # Match the API gate: auth is disabled for the current single-user test.
        if (_AUTH_ENABLED and not _auth.valid_session(request)
                and not full_path.startswith("login")):
            return RedirectResponse("/login", status_code=302)
        candidate = (WEB_OUT / full_path).resolve()
        if not candidate.is_relative_to(WEB_OUT.resolve()):
            raise HTTPException(404)  # traversal attempt — serve nothing
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        if full_path and (candidate / "index.html").is_file():
            return FileResponse(candidate / "index.html")
        # static export writes /settings as settings.html, so a hard load or a
        # bookmark of /settings would otherwise fall through to the dashboard
        if full_path and (sibling := (WEB_OUT / f"{full_path}.html")).is_file():
            return FileResponse(sibling)
        # route-specific shells: /project/<id> → its exported shell (client
        # reads the real id from the URL)
        parts = [p for p in full_path.split("/") if p]
        if parts and parts[0] == "project":
            for shell in (WEB_OUT / "project" / "0" / "index.html",
                          WEB_OUT / "project" / "0.html"):
                if shell.exists():
                    return FileResponse(shell)
        # Root is the dashboard. Anything else that matched no file above is a
        # genuinely unknown path — say so instead of silently serving the
        # dashboard with a 200 (which hid typos and broken bookmarks).
        if not full_path:
            return FileResponse(WEB_OUT / "index.html")
        raise HTTPException(404, "not found")
