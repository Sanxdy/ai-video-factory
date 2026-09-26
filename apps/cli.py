"""AVF CLI (spec §44-45) — entry point: `avf doctor`, `avf idea generate`, etc."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import data_dir
from core.logging import get_logger
from providers.llm import ensure_llm_ready

app = typer.Typer(help="AI Video Factory — local content production pipeline")
idea_app = typer.Typer(help="Idea generation")
produce_app = typer.Typer(help="Produce videos")
youtube_app = typer.Typer(help="YouTube upload/analytics")
analytics_app = typer.Typer(help="Analytics sync")
queue_app = typer.Typer(help="Content queue")
log = get_logger("cli")


@app.callback()
def main():
    """AI Video Factory CLI."""
    pass


@app.command("doctor")
def doctor():
    """Check environment, models, and pipeline readiness."""
    from core.system import run_doctor
    ok = run_doctor()
    raise typer.Exit(0 if ok else 1)


@idea_app.command("generate")
def idea_generate(count: int = typer.Option(5, help="Number of ideas")):
    """Generate N content ideas."""
    from apps.orchestrator.pipeline import get_db
    from apps.research.idea_engine import generate_ideas

    try:
        ensure_llm_ready()
    except Exception as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(1)
    db = get_db()
    ideas = generate_ideas(db, count=count)
    for i, idea in enumerate(ideas, 1):
        typer.echo(f"{i}. {idea.topic} — {idea.hook} ({idea.estimated_interest}/100)")


@produce_app.command("run")
def produce_run(project_id: int | None = None, dry_run: bool = False):
    """Produce a video: run the full pipeline for a project.

    Without --project-id, creates a fresh project first.
    """
    from apps.orchestrator.pipeline import create_project, get_db, run_pipeline

    db = get_db()
    if project_id is None:
        project_id = create_project()
        typer.echo(f"[NEW PROJECT] #{project_id}")
    end = run_pipeline(project_id, dry_run=dry_run)
    typer.echo(f"[DONE] project {project_id} → {end.value}")
    if dry_run:
        typer.echo("(dry-run: no models called)")


@produce_app.command("all")
def produce_all(dry_run: bool = False):
    """Produce videos for all incomplete projects (resume)."""
    from apps.orchestrator.pipeline import get_db, run_pipeline

    db = get_db()
    for p in db.incomplete_projects():
        if p["status"] in ("APPROVAL", "FAILED", "COMPLETE"):
            continue  # needs a human decision or is finished — not resumable
        typer.echo(f"[RESUME] project {p['id']} @ {p['status']}")
        end = run_pipeline(p["id"], dry_run=dry_run)
        typer.echo(f"  → {end.value}")


@produce_app.command("count")
def produce_count(count: int = typer.Argument(1, help="Number of videos")):
    """Produce N videos sequentially."""
    for i in range(count):
        typer.echo(f"--- Producing video {i+1}/{count} ---")
        produce_run(project_id=None)


@app.command("status")
def status():
    """Show DB summary: projects, ideas, scripts, scenes, jobs."""
    from apps.orchestrator.pipeline import get_db

    db = get_db()
    def count(t): return len(db.all(t))
    typer.echo(json.dumps({
        "projects": count("projects"),
        "ideas": count("ideas"),
        "research": count("research"),
        "scripts": count("scripts"),
        "scenes": count("scenes"),
        "jobs": count("jobs"),
    }, indent=2))


@app.command("models")
def models():
    """List configured models and their status."""
    from core.model_manager import discover_models, verify_model

    for m in discover_models():
        status = "OK" if verify_model(m["name"]) else "MISSING"
        typer.echo(f"[{status}] {m['name']:20s} task={m['task']:7s} "
                   f"backend={m['backend']:8s} status={m['status']}")


@youtube_app.command("auth")
def youtube_auth():
    """OAuth flow to connect a YouTube channel."""
    from apps.uploader.youtube import oauth_flow
    oauth_flow()


@youtube_app.command("promo-backfill")
def youtube_promo_backfill(dry_run: bool = typer.Option(False, "--dry-run",
                                                        help="Report only, no writes"),
                           limit: int = typer.Option(60, "--limit",
                                                     help="Max videos to update per run. Each "
                                                          "update costs 50 quota units, so keep "
                                                          "this low enough that backfill + the "
                                                          "day's uploads stay under 10,000 units")):
    """Prepend the channel promo to every already-uploaded video's description,
    and swap a superseded promo block for the current copy.

    Walks the channel's uploads playlist, not the local `projects` table: most
    videos on the channel predate the pipeline's DB rows (146 of 251 tracked),
    so a DB walk silently skips them. Idempotent: a video already carrying the
    current copy is skipped, so a daily capped run walks the backlog without
    touching the same video twice.
    """
    from apps.uploader.youtube import apply_promo, list_channel_videos

    counts = {"updated": 0, "refreshed": 0, "skipped": 0, "dry-run": 0, "failed": 0}
    try:
        videos = list_channel_videos(max_results=1000)
    except Exception as e:
        # channels.list is the first call — an exhausted quota dies here, before
        # the per-video guard. Report a retry instead of a cron traceback.
        typer.echo(f"cannot enumerate the channel (quota?): {str(e)[:100]}")
        typer.echo("nothing changed — will retry after the 07:00 UTC reset.")
        return
    for v in videos:
        vid = v["id"]
        # updated and refreshed both call videos.update — same daily quota bucket
        if counts["updated"] + counts["refreshed"] >= limit:
            break
        try:
            res = apply_promo(vid, dry_run=dry_run)
        except Exception as e:
            res = "failed"
            typer.echo(f"{vid}: FAILED {e}")
        counts[res] = counts.get(res, 0) + 1
    typer.echo(f"done: {counts}")


@app.command("pending")
def pending():
    """List projects waiting for approval."""
    from apps.orchestrator.pipeline import get_db

    db = get_db()
    rows = db.all("projects", "status=?", ("APPROVAL",))
    if not rows:
        typer.echo("No projects awaiting approval.")
        return
    for p in rows:
        typer.echo(f"#{p['id']}  {p['status']}  script={p['script_id']}")


@app.command("approve")
def approve(project_id: int):
    """Approve a video → moves APPROVAL → UPLOADING."""
    from apps.orchestrator.pipeline import get_db
    from core.jobs import State

    db = get_db()
    p = db.get("projects", project_id)
    if not p or p["status"] != "APPROVAL":
        typer.echo(f"Project {project_id} is not awaiting approval.")
        raise typer.Exit(1)
    db.update("projects", project_id, status=State.UPLOADING.value)
    typer.echo(f"[APPROVED] project {project_id} → UPLOADING")


@app.command("reject")
def reject(project_id: int, reason: str = ""):
    """Reject a video → back to EDITING for re-render (or FAILED if no reason)."""
    from apps.orchestrator.pipeline import get_db
    from core.jobs import State

    db = get_db()
    p = db.get("projects", project_id)
    if not p or p["status"] != "APPROVAL":
        typer.echo(f"Project {project_id} is not awaiting approval.")
        raise typer.Exit(1)
    target = State.EDITING if reason else State.FAILED
    db.update("projects", project_id, status=target.value)
    typer.echo(f"[REJECTED] project {project_id} → {target.value}"
               + (f" ({reason})" if reason else ""))


@app.command("upload")
def upload(project_id: int, privacy: str = typer.Option("public", help="public|unlisted|private")):
    """Upload a completed project's video to YouTube."""
    from apps.orchestrator.pipeline import get_db
    from apps.uploader.youtube import upload_video

    db = get_db()
    p = db.get("projects", project_id)
    if not p:
        typer.echo(f"Project {project_id} not found.")
        raise typer.Exit(1)
    video = data_dir() / "content" / "rendered" / f"project-{project_id:05d}" / "final.mp4"
    if not video.exists():
        typer.echo(f"No video at {video}")
        raise typer.Exit(1)
    idea = db.get("ideas", p.get("idea_id")) if p.get("idea_id") else None
    # Generate tags for SEO
    topic = (idea or {}).get("topic") or f"AI Video {project_id}"
    hook = (idea or {}).get("hook", "")
    try:
        from apps.research.title_gen import generate_titles
        meta = generate_titles(topic, hook)
        tags = meta.get("tags", [])
    except Exception:
        tags = []
    vid = upload_video(video,
                       title=topic,
                       description=hook,
                       tags=tags,
                       privacy=privacy)
    db.update("projects", project_id, youtube_id=vid)
    typer.echo(f"[UPLOADED] https://youtube.com/watch?v={vid} ({privacy})")


@analytics_app.command("sync")
def analytics_sync():
    """Pull YouTube stats for all uploaded projects + rescore idea queue."""
    from apps.analytics.collector import collect_for_project
    from apps.analytics.feedback import rescore_pending_ideas
    from apps.orchestrator.pipeline import get_db

    db = get_db()
    n = 0
    for p in db.all("projects"):
        if p.get("youtube_id"):
            stats = collect_for_project(db, p["id"])
            typer.echo(f"#{p['id']} {p['youtube_id']}: {stats}")
            n += 1
    rescored = rescore_pending_ideas(db)
    typer.echo(f"synced {n} videos; rescored {rescored} pending ideas")


@queue_app.command("generate")
def queue_generate(count: int = typer.Option(20)):
    """Generate ideas → score → dedupe → keep best N in the queue."""
    from apps.research.idea_engine import generate_ideas
    from apps.orchestrator.pipeline import get_db

    db = get_db()
    generate_ideas(db, count=count)
    # dedupe by normalized topic, keep highest score
    seen: dict[str, dict] = {}
    for idea in db.all("ideas", "status=?", ("NEW",)):
        key = " ".join(sorted(set(idea["topic"].lower().split())))
        if key in seen:
            dup = seen[key]
            keep, drop = ((idea, dup) if (idea.get("score") or 0) >= (dup.get("score") or 0)
                          else (dup, idea))
            db.delete("ideas", drop["id"])
            seen[key] = keep
        else:
            seen[key] = idea
    ranked = sorted(seen.values(), key=lambda i: i.get("score") or 0, reverse=True)
    for rank, idea in enumerate(ranked[:count], 1):
        typer.echo(f"{rank}. [{idea.get('score', '?')}] {idea['topic']}")
    typer.echo(f"queue ready: {min(len(ranked), count)} ideas")


@app.command("research")
def research_cmd(idea_id: int):
    """Research one idea → store facts + confidence."""
    from apps.research.research_agent import research_idea
    from apps.orchestrator.pipeline import get_db

    out = research_idea(get_db(), idea_id)
    typer.echo(json.dumps(out, indent=2, default=str)[:800])


@app.command("script")
def script_cmd(idea_id: int):
    """Generate a script for an idea."""
    from apps.scripting.script_gen import generate_script
    from apps.orchestrator.pipeline import get_db

    sid = generate_script(get_db(), idea_id)
    typer.echo(f"[SCRIPT] id={sid} for idea {idea_id}")


@app.command("storyboard")
def storyboard_cmd(script_id: int):
    """Generate storyboard scenes for a script."""
    from apps.storyboard.storyboard_gen import generate_storyboard
    from apps.orchestrator.pipeline import get_db

    ids = generate_storyboard(get_db(), script_id)
    typer.echo(f"[STORYBOARD] {len(ids)} scenes for script {script_id}")


@app.command("render")
def render_cmd(project_id: int):
    """Re-render final.mp4 for a project (EDITING → QC)."""
    from apps.orchestrator.pipeline import get_db, run_pipeline

    db = get_db()
    db.update("projects", project_id, status="EDITING")
    end = run_pipeline(project_id)
    typer.echo(f"[RENDER] project {project_id} → {end.value}")


@app.command("quality")
def quality_cmd(project_id: int):
    """Run the quality gate on a project's final.mp4."""
    from apps.quality.quality_gate import evaluate_video

    video = data_dir() / "content" / "rendered" / f"project-{project_id:05d}" / "final.mp4"
    result = evaluate_video(video)
    typer.echo(json.dumps(result, indent=2, default=str)[:800])


@app.command("preview")
def preview_cmd(project_id: int):
    """Show project summary + where the video lives."""
    from apps.orchestrator.pipeline import get_db

    db = get_db()
    p = db.get("projects", project_id)
    if not p:
        typer.echo(f"Project {project_id} not found.")
        raise typer.Exit(1)
    idea = db.get("ideas", p.get("idea_id")) if p.get("idea_id") else None
    video = data_dir() / "content" / "rendered" / f"project-{project_id:05d}" / "final.mp4"
    typer.echo(f"project #{project_id}  status={p['status']}")
    typer.echo(f"topic: {(idea or {}).get('topic', '?')}")
    typer.echo(f"video: {video} ({'exists' if video.exists() else 'MISSING'})")
    if p.get("youtube_id"):
        typer.echo(f"youtube: https://youtube.com/watch?v={p['youtube_id']}")


@app.command("daemon")
def daemon(interval: int = typer.Option(900, help="Seconds between cycles"),
           target: int = typer.Option(5, help="Videos awaiting approval"),
           cycles: int = typer.Option(1, help="Max cycles (0 = infinite)"),
           videos_per_day: int = typer.Option(3, help="Max new videos/day"),
           start_hour: int = typer.Option(1, help="Production window start"),
           end_hour: int = typer.Option(7, help="Production window end")):
    """Autonomous production loop: analytics → produce → QC → wait approval."""
    from apps.orchestrator.daemon import run_daemon

    run_daemon(interval=interval, target=target,
               max_cycles=cycles if cycles > 0 else None,
               videos_per_day=videos_per_day,
               start_hour=start_hour, end_hour=end_hour)


@app.command("daily")
def daily():
    """Auto-produce today's scheduled video (upload.schedule, Mon–Sun themes)."""
    from apps.uploader.daily import main as daily_main
    raise typer.Exit(daily_main())


@app.command("cleanup")
def cleanup(days: int = typer.Option(7, help="Retention period in days (delete completed videos older than N days)")):
    """Retention cleanup: purge old project media & DB entries to free disk space."""
    from core.cleanup import run_retention_cleanup
    from core.settings import get_db

    db = get_db()
    res = run_retention_cleanup(db, days=days)
    typer.echo(f"[CLEANUP] Deleted {res['deleted_count']} project(s) older than {days} days; "
               f"trimmed media from {res['trimmed_count']} published project(s). "
               f"Freed {res['freed_mb']} MB.")
    raise typer.Exit(0)


@app.command("produce", hidden=True)
def _produce_fallback(count: int = 1, dry_run: bool = False):
    """Fallback: `avf produce` → same as `avf produce run`."""
    produce_run(project_id=None, dry_run=dry_run)


app.add_typer(idea_app, name="idea", help="Idea commands")
app.add_typer(produce_app, name="produce", help="Produce commands")
app.add_typer(youtube_app, name="youtube", help="YouTube commands")
app.add_typer(analytics_app, name="analytics", help="Analytics commands")
app.add_typer(queue_app, name="queue", help="Content queue commands")


@app.command("serve")
def serve(host: str = "0.0.0.0", port: int = 8600):
    """Web UI + REST API (spec: Browser UI → FastAPI → Job System)."""
    import uvicorn
    from apps.api.server import app as api_app

    uvicorn.run(api_app, host=host, port=port, log_level="warning")


def main_entry():
    app()


if __name__ == "__main__":
    main_entry()