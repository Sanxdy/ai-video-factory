"""Pipeline orchestration — wires DB + real stage handlers into Orchestrator."""
from __future__ import annotations

from pathlib import Path

from core.config import config, data_dir
from core.database import Database, _now  # UTC-aware — keep timestamps comparable
from core.errors import QuotaError
from core.jobs import Orchestrator, StageResult, State
from core.logging import get_logger

log = get_logger("pipeline")


def get_db() -> Database:
    return Database(config.db_path)


_MOOD_BY_KEYWORD = [
    # (playlist keyword → mood bed in the music folder; names match the .mp3
    # basenames, so adding a bed means adding a row here to let it be chosen)
    ("cat", "playful"), ("comedy", "playful"), ("hack", "playful"),
    ("space", "cosmic"), ("science", "cosmic"),
    ("wild", "nature"), ("animal", "nature"), ("weather", "rain"),
    ("adrenaline", "energetic"), ("sport", "energetic"),
    ("boost", "uplifting"), ("motiv", "uplifting"),
    ("mystery", "mystery"), ("crime", "mystery"), ("unsolved", "mystery"),
    ("creepy", "horror"), ("dark", "horror"), ("scary", "horror"),
    ("tech", "scifi"), ("future", "scifi"), ("artificial", "scifi"),
    ("war", "epic"), ("history", "epic"), ("battle", "epic"),
    ("study", "lofi"), ("relax", "lofi"), ("focus", "lofi"),
    ("secret", "suspense"), ("conspiracy", "suspense"), ("investigat", "suspense"),
]


def _mood_for_playlist(playlist: str) -> str:
    """Map a daily-theme playlist name to a mood BGM bed (default: calm)."""
    pl = playlist.lower()
    for kw, mood in _MOOD_BY_KEYWORD:
        if kw in pl:
            return mood
    return "calm"


def _project_dir(project_id: int) -> Path:
    from core.filesystem import project_dir
    return project_dir(project_id)


def _make_scene_srt(scenes: list[dict], audio_paths: list[str | None],
                    out_path: Path) -> Path:
    """Build SRT from scene timeline — each scene = one caption aligned to its
    scene window (scene start → end). This keeps captions in lockstep with the
    visuals (Whisper chunking merges sentences and drifts from the edit)."""
    from apps.editor.subtitles import _wav_dur
    lines: list[str] = []
    idx = 1
    cursor = 0.0
    for sc, ap in zip(scenes, audio_paths):
        # caption window = real spoken length (caption hides when VO ends —
        # NEVER extend past the voice into the next scene's narration).
        real = _wav_dur(ap)
        dur = max(float(real if real else sc["duration"]), 0.25)
        # scene slot (cursor advance) = same padded duration the audio uses,
        # so the next caption starts exactly when the next VO starts.
        slot = (max(float(sc.get("duration", 4.0)),
                    float(real if real else sc.get("duration", 4.0))) + 0.25
                + float(sc.get("pause_after") or 0))
        text = (sc.get("narration") or "").strip()
        if text and ap:
            lines.append(str(idx))
            lines.append(f"{_fmt_srt_ts(cursor)} --> {_fmt_srt_ts(cursor + dur)}")
            lines.append(text)
            lines.append("")
            idx += 1
        cursor += slot
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _fmt_srt_ts(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_stages(project_id: int) -> dict[State, callable]:
    """All pipeline stage handlers bound to one project.

    Each handler returns StageResult(success, data, next?).
    Failures set project FAILED via Orchestrator.
    """
    from apps.research.idea_engine import generate_ideas, select_best_idea
    from apps.research.research_agent import research_idea
    from apps.scripting.script_gen import generate_hooks, generate_script
    from apps.storyboard.storyboard_gen import generate_storyboard
    from providers.image import ImageProvider

    db = get_db()
    state: dict = {}  # runtime data across stages

    def _resolve_script_id(pid):
        """Find script id: state → project row → DB fallback."""
        if state.get("script_id"):
            return state["script_id"]
        proj = db.get("projects", pid)
        if proj and proj.get("script_id"):
            return proj["script_id"]
        ideas = db.all("ideas", "status IN ('SCRIPTED','RESEARCHED')")
        for idea in ideas:
            sc = db.all("scripts", "idea_id=?", (idea["id"],))
            if sc:
                return sc[0]["id"]
        return None

    def _load_scenes(pid):
        """Scenes for a project's script, sorted by scene_number."""
        sid = _resolve_script_id(pid)
        if not sid:
            return []
        scenes = db.all("scenes", "script_id=?", (sid,))
        scenes.sort(key=lambda s: s["scene_number"])
        return sid, scenes

    def stage_idea(pid, st):
        # a user-seeded topic (create_project) must win over the shared NEW
        # pool: queued projects all seed score-100 ideas, so the pool's
        # oldest-row tiebreak would hand them a leftover idea instead
        seed_id = (db.get("projects", pid) or {}).get("idea_id")
        seed = db.get("ideas", seed_id) if seed_id else None
        idea = [seed] if seed else select_best_idea(db, 1)
        if not idea:
            ideas = generate_ideas(db, count=1)
            if not ideas:
                return StageResult(stage=st, success=False, error="no ideas generated")
            idea_id = db.insert("ideas", topic=ideas[0].topic, source="llm",
                                score=ideas[0].estimated_interest, status="NEW",
                                created_at=_now())
        else:
            idea_id = idea[0]["id"]
        # claim it so the next project generates/picks a fresh idea
        db.update("ideas", idea_id, status="SELECTED")
        # stage_idea stores idea_id on the project row (persisted for resume)
        db.update("projects", pid, idea_id=idea_id)
        state["idea_id"] = idea_id
        return StageResult(stage=st, success=True, data={"idea_id": idea_id})

    def _resolve_idea_id(pid) -> int:
        """Find the idea for a project: memory state → DB fallback."""
        if state.get("idea_id"):
            return state["idea_id"]
        # resumed runs skip stage_idea — the persisted link is authoritative
        proj = db.get("projects", pid) or {}
        if proj.get("idea_id"):
            return proj["idea_id"]
        # legacy rows predating idea_id persistence (pipeline creates the
        # idea first, so newest wins)
        rows = db.all("ideas")
        if not rows:
            raise RuntimeError(f"project {pid}: no idea found in DB")
        return max(r["id"] for r in rows)

    def stage_research(pid, st):
        out = research_idea(db, _resolve_idea_id(pid))
        return StageResult(stage=st, success=True, data=out)

    def stage_hooks(pid, st):
        out = generate_hooks(db, _resolve_idea_id(pid))
        return StageResult(stage=st, success=True, data=out)

    def stage_script(pid, st):
        from core.settings import get_setting, set_setting
        # a revision note (set by POST /api/projects/{pid}/revise) drives exactly
        # one regeneration: consumed here, carried to the storyboard via state
        note = (get_setting(f"project.{pid}.revision_note") or "").strip()
        if note:
            set_setting(f"project.{pid}.revision_note", "")
            state["revision_note"] = note
        sid = generate_script(db, _resolve_idea_id(pid), project_id=pid,
                              revision_note=note)
        state["script_id"] = sid
        # an auto voice resolves NOW: the script's text is what determines the
        # video's language, and the voice has to match it. Stored per project so
        # stage_audio's TTS uses it and a resume re-resolves identically.
        from providers.tts.kokoro import resolve_auto_voice
        script_row = db.get("scripts", sid) or {}
        spoken = " ".join(filter(None, (script_row.get("hook"),
                                        script_row.get("body"),
                                        script_row.get("cta"))))
        if spoken.strip():
            set_setting(f"project.{pid}.voice",
                        resolve_auto_voice(get_setting("tts.voice") or "auto-female",
                                           spoken))
        # persist script id on project for resume (was 0 → broke resume)
        db.update("projects", pid, script_id=sid)
        return StageResult(stage=st, success=True, data={"script_id": sid})

    def stage_storyboard(pid, st):
        sid = _resolve_script_id(pid)  # resume-safe: state dict is empty on retry
        scene_ids = generate_storyboard(db, sid, project_id=pid,
                                        revision_note=state.get("revision_note", ""))
        state["script_id"] = sid
        return StageResult(stage=st, success=True, data={"scenes": len(scene_ids)})

    def stage_assets(pid, st):
        """GENERATING_ASSETS → stock video clips (if enabled) or scene images."""
        from core.filesystem import project_dir

        sid, scenes = _load_scenes(pid)
        if not scenes:
            return StageResult(stage=st, success=False, error="no scenes for project")
        state["script_id"] = sid
        state["scenes"] = scenes

        from providers.stock import fetch_scene_stock, stock_enabled
        from apps.editor.editor import resolution
        ratio = (db.get("projects", pid) or {}).get("aspect_ratio")
        orient = "landscape" if ratio == "16:9" else "portrait"
        img = ImageProvider(resolution=resolution(ratio))
        pdir = project_dir(pid)
        from core.jobs import is_cancel_requested
        from core.progress import set_progress
        paths = []
        n_stock = 0
        # Clips already consumed by this project — a project must never reuse
        # the same clip across scenes. Keyed "provider:id" so Pexels id 123 and
        # Pixabay id 123 stay distinct. Sidecars on disk survive resumes.
        used_stock: set = set()
        for f in list((pdir / "assets").glob("*.pexels-id")) + \
                list((pdir / "assets").glob("*.pixabay-id")):
            provider = f.suffix.lstrip(".").removesuffix("-id")
            val = f.read_text().strip()
            if val:
                used_stock.add(f"{provider}:{val}")
        for i, sc in enumerate(scenes, 1):
            # let Cancel land between scenes instead of only at stage boundaries
            if is_cancel_requested(pid):
                state["asset_paths"] = paths
                return StageResult(stage=st, success=True, next=State.CANCELLED)
            set_progress(pid, "GENERATING_ASSETS",
                         55 + round(14 * (i - 1) / len(scenes)),
                         f"Scene asset {i}/{len(scenes)}")
            base = pdir / "assets" / f"scene-{sc['scene_number']:02d}"
            base.parent.mkdir(parents=True, exist_ok=True)
            # user-imported clips (Gemini/Veo etc.) win over every provider
            existing_mp4 = base.with_suffix(".mp4")
            if existing_mp4.exists():
                paths.append(str(existing_mp4))
                n_stock += 1  # counts toward the motion-asset tally
                db.update("scenes", sc["id"], status="ASSET_READY")
                continue
            asset = None
            if stock_enabled():
                mp4 = base.with_suffix(".mp4")
                if not mp4.exists():
                    from providers.stock import fetch_scene_stock
                    try:
                        fetch_scene_stock(sc, mp4, exclude_ids=used_stock,
                                          orientation=orient)
                    except Exception as e:
                        log.warning("stock video miss scene %s: %s",
                                    sc["scene_number"], e)
                    for ext in (".pexels-id", ".pixabay-id"):
                        side = mp4.with_suffix(ext)
                        if side.exists():
                            val = side.read_text().strip()
                            if val:
                                used_stock.add(
                                    f"{ext.lstrip('.').removesuffix('-id')}:{val}")
                if mp4.exists():
                    asset = str(mp4)
                    n_stock += 1
            if asset is None:
                png = base.with_suffix(".png")
                if not png.exists():
                    img.generate(sc["visual_prompt"], png)
                asset = str(png)
            paths.append(asset)
            db.update("scenes", sc["id"], status="ASSET_READY")
        state["asset_paths"] = paths
        return StageResult(stage=st, success=True,
                           data={"assets": len(paths), "stock_videos": n_stock})

    def stage_audio(pid, st):
        """GENERATING_AUDIO → per-scene narration via TTS."""
        from providers.tts.kokoro import KokoroTTS
        from core.filesystem import project_dir

        _sid, scenes = _load_scenes(pid)
        state["scenes"] = scenes
        tts = KokoroTTS()
        pdir = project_dir(pid)
        audio_paths = []
        for sc in scenes:
            narration = sc["narration"] or ""  # schema allows NULL
            if not narration.strip():
                audio_paths.append(None)
                continue
            out = pdir / "audio" / f"scene-{sc['scene_number']:02d}.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.exists():
                tts.synth(narration, out)
            # real audio length beats the storyboard's estimate — the edit
            # stage sizes clips from these durations
            import wave
            with wave.open(str(out)) as w:
                dur = w.getnframes() / w.getframerate()
            if abs(dur - (sc["duration"] or 0)) > 0.2:
                db.update("scenes", sc["id"], duration=round(dur, 2))
            audio_paths.append(str(out))
        state["audio_paths"] = audio_paths
        return StageResult(stage=st, success=True, data={"audio": len([a for a in audio_paths if a])})

    def stage_subtitles(pid, st):
        """GENERATING_SUBTITLES → scene-locked captions (SRT or ASS preset)."""
        from core.filesystem import project_dir
        from apps.editor.subtitles import build_ass, subtitle_preset

        pdir = project_dir(pid)
        _sid, scenes = _load_scenes(pid)
        state["scenes"] = scenes
        # derive audio paths from disk (resume-safe, not memory state)
        audio_paths = []
        for sc in scenes:
            ap = pdir / "audio" / f"scene-{sc['scene_number']:02d}.wav"
            audio_paths.append(str(ap) if ap.exists() else None)
        state["audio_paths"] = audio_paths
        if subtitle_preset() == "classic":
            subs = _make_scene_srt(scenes, audio_paths, pdir / "subtitles.srt")
        else:
            subs = build_ass(scenes, audio_paths, pdir / "subtitles.ass")
        state["subtitles"] = str(subs)
        return StageResult(stage=st, success=True, data={"srt": str(subs)})

    def stage_edit(pid, st):
        """EDITING → render final MP4 (clips + audio + subs + loudnorm)."""
        from apps.editor.media_pipeline import produce_video

        _sid, scenes = _load_scenes(pid)
        state["scenes"] = scenes
        # derive asset paths from disk (resume-safe); .mp4 = stock footage,
        # .png = AI image — media pipeline picks the right clip builder
        from core.filesystem import project_dir
        from apps.editor.editor import resolution
        ratio = (db.get("projects", pid) or {}).get("aspect_ratio")
        img = ImageProvider(resolution=resolution(ratio))
        pdir = project_dir(pid)
        paths = []
        for sc in scenes:
            base = pdir / "assets" / f"scene-{sc['scene_number']:02d}"
            mp4, png = base.with_suffix(".mp4"), base.with_suffix(".png")
            if not mp4.exists() and not png.exists():
                img.generate(sc["visual_prompt"], png)
            paths.append(str(mp4 if mp4.exists() else png))
        state["asset_paths"] = paths

        scenes = [dict(s, image=str(p), video=str(p) if p.endswith(".mp4") else None)
                  for s, p in zip(state["scenes"], state["asset_paths"])]
        # read captions from disk (state doesn't persist across runs);
        # preset may be .srt (classic) or .ass (styled)
        subs_path = pdir / "subtitles.srt"
        if not subs_path.exists():
            subs_path = pdir / "subtitles.ass"
        subs = subs_path if subs_path.exists() else None
        # ponytail: rebuild reuses the last render's voice track so swapping
        # clips never re-runs TTS — but ONLY when this run didn't (re)build
        # per-scene audio. If stage_audio ran, narration.wav is either gone or
        # stale; reusing it silently desyncs VO vs subtitles (project 14).
        prev_voice = data_dir() / "content" / "rendered" / f"project-{pid:05d}" / "narration.wav"
        fresh_audio = bool(state.get("audio_paths"))
        narration = prev_voice if (prev_voice.exists() and not fresh_audio) else None
        from core.settings import get_setting
        from apps.editor.music import resolve_bed
        music_ov = get_setting(f"project.{pid}.music")
        if music_ov:
            music_path = resolve_bed(music_ov)
        else:
            # mood-matched bed: pick by playlist (= daily theme), fallback default
            mood = _mood_for_playlist(get_setting(f"project.{pid}.playlist") or "")
            music_path = resolve_bed(f"{mood}.mp3")
        # per-scene narration from stage_audio on disk — reuse so VO matches the
        # wavs the subtitles were timed against (Kokoro re-synth is
        # non-deterministic and desyncs). Resume-safe: read disk, not memory.
        scene_audio = []
        for scn in state["scenes"]:
            ap = pdir / "audio" / f"scene-{scn['scene_number']:02d}.wav"
            scene_audio.append(ap if ap.exists() else None)
        info = produce_video(pid, scenes, narration_path=narration,
                             subtitles_path=subs, music_path=music_path,
                             scene_audio=scene_audio, ratio=ratio)
        state["video_info"] = info
        return StageResult(stage=st, success=True, data=info)

    def stage_quality(pid, st):
        """QUALITY_CHECK → run technical gate on rendered video.

        Fails the stage (→ FAILED) if the video misses hard requirements.
        """
        from apps.quality.quality_gate import QualityGate

        video = data_dir() / "content" / "rendered" / f"project-{pid:05d}" / "final.mp4"
        proj = db.get("projects", pid) or {}
        gate = QualityGate(video, aspect_ratio=proj.get("aspect_ratio"))
        issues = gate.check_technical()
        if issues:
            return StageResult(stage=st, success=False,
                               error="QC failed: " + "; ".join(issues))
        return StageResult(stage=st, success=True,
                           data={"qc": "pass", "video": str(video)})

    def stage_upload(pid, st):
        """UPLOADING → publish to YouTube (requires `avf approve` first)."""
        from apps.uploader.youtube import upload_video

        video = data_dir() / "content" / "rendered" / f"project-{pid:05d}" / "final.mp4"
        if not video.exists():
            return StageResult(stage=st, success=False,
                               error=f"missing video: {video}")
        # title/description: approval-card overrides win, else LLM (§33)
        from apps.research.title_gen import generate_titles
        from core.settings import get_setting, set_setting

        proj = db.get("projects", pid)
        idea = db.get("ideas", proj["idea_id"]) if proj and proj.get("idea_id") else None
        topic = (idea or {}).get("topic") or f"AI Video Project {pid}"
        hook = (idea or {}).get("hook", "")
        t_ov = get_setting(f"project.{pid}.yt_title")
        d_ov = get_setting(f"project.{pid}.yt_desc")
        tags = []  # default: no tags
        hashtags = []  # default: no hashtags
        if t_ov and d_ov:
            title, desc = t_ov, d_ov
            meta = {}
        else:
            try:
                meta = generate_titles(topic, hook)
                fb_title = meta["best"]["title"] if meta.get("best") else topic
                fb_desc = meta.get("description") or hook
                fb_tags = meta.get("tags") or []
            except Exception:
                log.warning("title generation failed; falling back to topic")
                fb_title, fb_desc = topic, hook
                fb_tags = []
                meta = {}
            title = t_ov or fb_title
            desc = d_ov or fb_desc
            tags = fb_tags
        # Hashtags and keyword tags are BOTH derived from the LLM metadata, and
        # the long-form flow supplies its own title/description — so the override
        # branch never saw them and every 16:9 video shipped with no hashtags and
        # an empty keyword-tags field. Fill whichever is missing from one extra
        # call (existing_titles=[] skips the channel lookup; dedup is irrelevant
        # for tags). Best-effort: a failed call must never block an upload.
        hashtags = meta.get("hashtags", [])
        if not hashtags or not tags:
            try:
                extra = generate_titles(topic, hook, existing_titles=[])
                hashtags = hashtags or (extra.get("hashtags") or [])
                tags = tags or (extra.get("tags") or [])
            except Exception:
                log.warning("hashtags/tags unavailable for project %d", pid)
        if hashtags:
            hashtag_line = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
            if hashtag_line not in desc:
                desc = desc.rstrip() + "\n\n" + hashtag_line
        # 9:16 projects get #Shorts tag for YouTube discovery
        proj_ratio = (db.get("projects", pid) or {}).get("aspect_ratio", "9:16")
        if proj_ratio == "9:16":
            if "#Shorts" not in desc:
                desc = desc.rstrip() + "\n\n#Shorts"
            if "#Shorts" not in tags:
                tags.append("#Shorts")
        try:
            pub = get_setting(f"project.{pid}.publish_at") or ""
            privacy = get_setting(f"project.{pid}.privacy") or "public"
            vid = upload_video(video, title=title, description=desc,
                               tags=tags, privacy=privacy, publish_at=pub)
            for k in ("publish_at", "yt_title", "yt_desc", "privacy"):
                if get_setting(f"project.{pid}.{k}"):
                    set_setting(f"project.{pid}.{k}", "")  # one-shot
            # playlist (one-shot): name is auto-created if missing
            pl_name = get_setting(f"project.{pid}.playlist")
            if pl_name:
                from apps.uploader.youtube import add_to_playlist, create_playlist
                add_to_playlist(create_playlist(pl_name), vid)
                set_setting(f"project.{pid}.playlist", "")
            # 16:9 long-form only. Shorts do not render a custom thumbnail in
            # the feed, and the Shorts path must stay unchanged — so this is
            # deliberately not wired for 9:16.
            if proj_ratio == "16:9":
                try:
                    from apps.editor.thumbnail import make_thumbnail
                    from apps.uploader.youtube import set_thumbnail
                    thumb = video.parent / "thumbnail.jpg"
                    make_thumbnail(video, thumb, title=title)
                    if thumb.exists():
                        set_thumbnail(vid, thumb)
                except Exception as e:
                    log.warning("thumbnail skipped for %d: %s", pid, e)
        except RuntimeError as e:  # no credentials — pause for setup
            return StageResult(stage=st, success=False, error=str(e))
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many Requests" in err:
                # Quota exhaustion is transient. Return to APPROVAL so the
                # held-upload cron can retry after the daily Pacific reset,
                # instead of leaving the project stuck in UPLOADING.
                from core.errors import QuotaError
                raise QuotaError("YouTube upload quota exhausted — retry after 07:00 UTC")
            return StageResult(stage=st, success=False, error=err)
        db.update("projects", pid, youtube_id=vid)
        return StageResult(stage=st, success=True, data={"youtube_id": vid})

    return {
        State.IDEA: stage_idea,
        State.RESEARCHING: stage_research,
        State.SCRIPTING: stage_script,
        State.STORYBOARDING: stage_storyboard,
        State.GENERATING_ASSETS: stage_assets,
        State.GENERATING_AUDIO: stage_audio,
        State.GENERATING_SUBTITLES: stage_subtitles,
        State.EDITING: stage_edit,
        State.QUALITY_CHECK: stage_quality,
        State.UPLOADING: stage_upload,
    }


def create_project(topic: str | None = None) -> int:
    """Create a project row. With a user topic, pre-seed the idea so
    stage_idea picks it (score 100 beats any LLM idea) and skips the LLM."""
    db = get_db()
    idea_id = None
    if topic and topic.strip():
        raw_topic = topic.strip()
        # Deduplication: check both exact match and similarity against all existing topics
        from apps.research.idea_engine import _is_duplicate, _get_existing_topics
        existing_topics = _get_existing_topics(db)
        if _is_duplicate(raw_topic, existing_topics):
            import time
            raw_topic = f"{raw_topic} ({int(time.time())})"
            log.info('create_project: topic was duplicate, suffixed to %s', raw_topic[:60])
        idea_id = db.insert("ideas", topic=raw_topic, source="user", score=100,
                            status="NEW", created_at=_now())
    return db.insert("projects", script_id=0, status="IDEA", idea_id=idea_id,
                     created_at=_now())


def write_manifest(project_id: int) -> Path:
    """Project manifest (spec §24): everything needed to reproduce a video."""
    import json

    db = get_db()
    proj = db.get("projects", project_id) or {}
    pdir = _project_dir(project_id)

    idea = db.get("ideas", proj["idea_id"]) if proj.get("idea_id") else None
    script = db.get("scripts", proj["script_id"]) if proj.get("script_id") else None
    scenes = db.all("scenes", "script_id=?", (proj["script_id"],)) \
        if proj.get("script_id") else []

    manifest = {
        "project_id": project_id,
        "status": proj.get("status"),
        "created_at": proj.get("created_at"),
        "youtube_id": proj.get("youtube_id"),
        "idea": idea,
        "script": script,
        "scenes": scenes,
        "artifacts": {
            "video": str((data_dir() / "content" / "rendered" /
                          f"project-{project_id:05d}" / "final.mp4")),
            "subtitles": str(pdir / "subtitles.srt"),
            "audio_dir": str(pdir / "audio"),
            "assets_dir": str(pdir / "assets"),
        },
    }
    pdir.mkdir(parents=True, exist_ok=True)
    out = pdir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, default=str))
    return out


def run_pipeline(project_id: int, dry_run: bool = False) -> State:
    """Execute full pipeline on a project. Returns final state."""
    from core.progress import clear_progress, set_progress

    db = get_db()
    proj = db.get("projects", project_id) or {}
    stages = build_stages(project_id)

    # storyboard-only projects skip asset/audio/subtitle generation on resume
    # (user uploads scene videos manually, then render starts at EDITING)
    if proj.get("storyboard_only") and proj.get("status") == "STORYBOARDED":
        from apps.editor.media_pipeline import produce_video
        from core.filesystem import project_dir
        pdir = project_dir(project_id)
        scenes = db.all("scenes", "script_id=?", (proj["script_id"])) \
            if proj.get("script_id") else []
        scenes.sort(key=lambda s: s["scene_number"])
        # collect scene audio paths from uploaded assets
        scene_audio = []
        for sc in scenes:
            wav = pdir / "audio" / f"scene-{sc['scene_number']:02d}.wav"
            scene_audio.append(wav if wav.exists() else None)
        set_progress(project_id, "EDITING", 60, "Rendering storyboard video")
        try:
            produce_video(project_id, scenes, out_root=pdir,
                         scene_audio=scene_audio)
            db.update("projects", project_id, status="RENDERED")
            from core import events
            events.emit("project_state", project_id=project_id, state="RENDERED")
        except Exception as e:
            log.error("storyboard render failed: %s", e)
            db.update("projects", project_id, status="FAILED")
            raise
        finally:
            clear_progress(project_id)
        # continue pipeline from QUALITY_CHECK
        pct = {"QUALITY_CHECK": 95, "UPLOADING": 98}
        wrapped = {}
        for st, fn in stages.items():
            if st in (State.QUALITY_CHECK, State.UPLOADING):
                def handler(pid, s, _fn=fn):
                    set_progress(pid, s.value, pct.get(s.value, 50))
                    return _fn(pid, s)
                wrapped[st] = handler
        orch = Orchestrator(db, wrapped)
        orch._set_state(project_id, State.QUALITY_CHECK)
        try:
            end = orch.run(project_id, dry_run=dry_run)
        except QuotaError:
            # Quota errors are transient: leave the project in the state
            # returned by the orchestrator (usually APPROVAL) so the cron
            # retry can pick it up after the daily reset.
            proj_after = db.get("projects", project_id) or {}
            end = State(proj_after["status"]) if proj_after else State.FAILED
            log.warning("quota error for project %s: will retry later (state=%s)",
                        project_id, end.value)
        finally:
            clear_progress(project_id)
        try:
            write_manifest(project_id)
        except Exception:
            log.warning("manifest write failed for %s", project_id)
        return end
    # wrap every handler so the UI sees percent + stage live (SSE via bus)
    pct = {"IDEA": 5, "RESEARCHING": 15, "SCRIPTING": 30, "STORYBOARDING": 40,
           "GENERATING_ASSETS": 55, "GENERATING_AUDIO": 70,
           "GENERATING_SUBTITLES": 80, "EDITING": 60, "QUALITY_CHECK": 95,
           "UPLOADING": 98}
    wrapped: dict[State, callable] = {}
    for st, fn in stages.items():
        def handler(pid, s, _fn=fn):
            set_progress(pid, s.value, pct.get(s.value, 50))
            return _fn(pid, s)
        wrapped[st] = handler
    orch = Orchestrator(db, wrapped)
    try:
        end = orch.run(project_id, dry_run=dry_run)
    finally:
        clear_progress(project_id)
    try:
        write_manifest(project_id)
    except Exception:
        log.warning("manifest write failed for %s", project_id)
    return end