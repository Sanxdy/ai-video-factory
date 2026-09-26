"""Media assembly pipeline — scenes → assets → clips → concat → audio → final.

Spec §18-19: the editor consumes storyboard scenes + generated assets.
This wires: image gen → Ken Burns clip → per-scene TTS → concat → narration
mix → subtitles → loudnorm → final MP4.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import config, data_dir
from core.logging import get_logger
from providers.image import ImageProvider
from providers.tts.kokoro import KokoroTTS
from providers.stt.whisper import WhisperSTT
from apps.editor.editor import (FFMPEG, dims, render_clip, video_clip, concat_clips,
                                merge_audio, burn_subtitles, loudness_normalize,
                                probe)

log = get_logger("media")


def produce_video(project_id: int, scenes: list[dict], out_root: Path | None = None,
                  narration_path: Path | None = None,
                  subtitles_path: Path | None = None,
                  music_path: Path | None = None,
                  scene_audio: list[Path | None] | None = None,
                  ratio: str | None = "9:16") -> dict:
    """Render full video from scenes. Returns artifact manifest.

    scenes: list of dicts with narration, motion_prompt, visual_prompt, duration.
    ratio: "9:16" (Shorts, default) or "16:9" (long-form).
    narration_path: pre-built narration track (avoids double build).
    subtitles_path: pre-built SRT (uses scene-locked timing from stage_subtitles).
    scene_audio: per-scene TTS wavs from stage_audio — the SINGLE source of
        truth for narration. When provided, produce_video does NOT re-synthesize
        (Kokoro is non-deterministic: a second pass yields different durations
        and desyncs VO vs subtitles — project 18/19).
    """
    out_root = out_root or (data_dir() / "content" / "rendered" / f"project-{project_id:05d}")
    out_root.mkdir(parents=True, exist_ok=True)
    from core.progress import set_progress

    # 1. per-scene assets
    w, h = dims(ratio)
    img_prov = ImageProvider(f"{w}x{h}")
    tts = KokoroTTS()
    clips: list[Path] = []
    narration_clips: list[Path | None] = []
    timeline: list[dict] = []

    for i, sc in enumerate(scenes, 1):
        set_progress(project_id, "EDITING", 60 + round(18 * (i - 1) / max(len(scenes), 1)),
                     f"Rendering scene {i}/{len(scenes)}")
        scene_dir = out_root / f"scene-{i:02d}"
        scene_dir.mkdir(exist_ok=True)

        # narration per scene: reuse stage_audio wav when provided (single TTS
        # source of truth — re-synthesis is non-deterministic and desyncs VO
        # vs subtitles), else synthesize now. Measured FIRST so the visual
        # clip below is rendered long enough to cover the spoken audio.
        narration = sc.get("narration", "").strip()
        pre = (scene_audio[i - 1] if scene_audio else None)
        if narration and pre and Path(pre).exists():
            n = Path(pre)
            if (scene_dir / "voice.wav").exists():
                (scene_dir / "voice.wav").unlink()  # stale from older build
            narration_clips.append(n)
        elif narration:
            n = tts.synth(narration, scene_dir / "voice.wav")
            narration_clips.append(n)
        else:
            narration_clips.append(None)
        # REAL spoken duration — scene slot must fit the voice, else the next
        # scene's VO overlaps this one (project 18/19 sync bug). LLM estimates
        # are ±30% off; the actual wav is ground truth.
        if narration_clips[-1] is not None:
            try:
                import wave
                with wave.open(str(narration_clips[-1]), "rb") as w:
                    voice_dur = w.getnframes() / w.getframerate()
            except Exception:
                voice_dur = sc.get("duration", 4.0)
        else:
            voice_dur = sc.get("duration", 4.0)
        # effective scene duration = max(visual slot, voice length) + 0.25s pad
        # (pad lets the VO breathe before the next scene; when voice is shorter
        # than the visual slot we keep the slot — never shrink below estimate).
        # pause_after holds the shot silent between countdown items.
        eff_dur = (max(float(sc.get("duration", 4.0)), voice_dur) + 0.25
                   + float(sc.get("pause_after") or 0))

        # visual clip rendered at eff_dur — video MUST cover the narration
        # (else merge_audio -shortest trims the final VO → "kepotong")
        src_video = sc.get("video")
        if src_video and Path(src_video).exists():
            # real motion footage — normalize, loop-fill, mute
            clip = video_clip(Path(src_video), scene_dir / "clip.mp4", eff_dur,
                              text_overlay=sc.get("text_overlay", ""), ratio=ratio)
            img = scene_dir / "image.png" if (scene_dir / "image.png").exists() \
                else src_video
        else:
            img = img_prov.generate(sc.get("visual_prompt", "abstract background"),
                                    scene_dir / "image.png")
            clip = render_clip(img, scene_dir / "clip.mp4", eff_dur,
                               sc.get("motion_prompt", "zoom-in"),
                               text_overlay=sc.get("text_overlay", ""), ratio=ratio)
        clips.append(clip)

        timeline.append({
            "scene": i, "image": str(img), "clip": str(clip),
            "video": bool(src_video and Path(src_video).exists()),
            # "gradient" = no real footage for this scene. Recorded so the QC
            # gate can name the cause rather than reporting a frozen-frame
            # defect the encoder had nothing to do with.
            "source": "video" if (src_video and Path(src_video).exists())
                      else (img_prov.last_tier or "image"),
            "duration": eff_dur, "motion": sc.get("motion_prompt", "zoom-in"),
            "narration": str(narration_clips[-1]) if narration_clips[-1] else None,
        })

    # 2. concat visual clips
    set_progress(project_id, "EDITING", 80, "Concatenating clips")
    silent = concat_clips(clips, out_root / "video_silent.mp4")
    log.info("concatenated %d clips → %s", len(clips), silent.name)

    # 3. build narration track from per-scene clips (align to scene starts)
    if narration_path and Path(narration_path).exists():
        voice = Path(narration_path)
        log.info("using pre-built narration: %s", voice.name)
    else:
        voice = build_scene_audio(timeline, out_root / "narration.wav")

    # 3b. optional music bed, ducked under narration (spec §19-20)
    from apps.editor.music import find_music, mix_audio
    from apps.editor.audio_levels import get_volumes
    voice_volume, music_volume = get_volumes(lambda k: __import__("core.settings", fromlist=["get_setting"]).get_setting(k))
    bed = Path(music_path) if music_path and Path(music_path).exists() else find_music()
    if bed:
        log.info("using music bed: %s", bed.name)
        voice = mix_audio(voice, bed, out_root / "narration_mixed.wav",
                          voice_volume=voice_volume, music_volume=music_volume)

    # 4. merge audio under video
    set_progress(project_id, "EDITING", 85, "Merging narration audio")
    mixed = merge_audio(silent, voice, out_root / "video_mixed.mp4",
                        voice_volume=voice_volume, music_volume=music_volume)

    # 5. subtitles (use pre-built scene-locked SRT if provided)
    if subtitles_path and Path(subtitles_path).exists():
        subs = Path(subtitles_path)
        log.info("using pre-built scene-locked subtitles: %s", subs.name)
        set_progress(project_id, "EDITING", 90, "Burning subtitles")
        burned = burn_subtitles(mixed, subs, out_root / "video_subs.mp4")
        mixed = burned
    else:
        # fallback: Whisper on narration track (for backward compat)
        subs = None
        try:
            stt = WhisperSTT(model="small")
            subs = stt.to_srt(voice, out_root / "subtitles.srt")
            if subs and subs.exists():
                burned = burn_subtitles(mixed, subs, out_root / "video_subs.mp4")
                mixed = burned
        except Exception as e:
            log.warning("subtitles skipped: %s", e)

    # 6. watermark (settings-driven; no-op copy when disabled)
    set_progress(project_id, "EDITING", 96, "Applying watermark")
    from apps.editor import watermark as wm_mod
    from core.database import Database as _Database
    db = _Database(config.db_path)
    from apps.orchestrator.pipeline import get_db as _pipeline_get_db
    try:
        proj = _pipeline_get_db().get("projects", project_id)
        wm_ratio = proj.get("aspect_ratio") if proj else None
    except Exception:
        wm_ratio = None
    mixed = wm_mod.apply_watermark(mixed, out_root / "video_wm.mp4", db,
                                   ratio=wm_ratio or ratio)

    # 7. loudness normalize
    set_progress(project_id, "EDITING", 98, "Normalizing loudness")
    final = loudness_normalize(mixed, out_root / "final.mp4")

    info = probe(final)
    info["path"] = str(final)
    info["scenes"] = len(clips)
    info["timeline"] = timeline
    manifest = out_root / "manifest.json"
    manifest.write_text(json.dumps(info, indent=2))
    log.info("final: %s (%.1fs, %d scenes)", final, info["duration"], len(clips))
    return info


def build_scene_audio(timeline: list[dict], out_path: Path) -> Path:
    """Stitch per-scene narration clips at their scene start times.

    Uses concat, NOT `adelay` + `amix`: amix scales every input by 1/N and then
    renormalizes upward as inputs end (dropout_transition), so the narration
    started ~20*log10(N) dB down and ramped to full level over the video — for
    a 67-scene long-form that is a 36 dB fade-in, which buries the VO under the
    music bed. ffmpeg 4.2 has no `amix:normalize=0`, so each clip is padded to
    its slot and concatenated instead (levels identical to the source wavs).
    """
    import subprocess
    # ponytail: KokoroTTS is the only voice source and emits 24 kHz mono; the
    # aformat pins every branch to that so mixed-format timelines still concat.
    fmt = "aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono"
    inputs: list[str] = []
    filters: list[str] = []
    parts: list[str] = []
    idx = 0
    total = 0.0
    for t in timeline:
        dur = float(t.get("duration", 4.0))
        total += dur
        nar = t.get("narration")
        if nar:
            inputs += ["-i", str(nar)]
            filters.append(
                f"[{idx}:a]{fmt},apad,atrim=0:{dur:.3f},"
                f"asetpts=N/SR/TB[s{idx}]")
        else:
            filters.append(
                f"anullsrc=r=24000:cl=mono,{fmt},atrim=0:{dur:.3f},"
                f"asetpts=N/SR/TB[s{idx}]")
        parts.append(f"[s{idx}]")
        idx += 1

    if not inputs:
        # silence track
        import subprocess as sp
        sp.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                "anullsrc=r=24000:cl=mono", "-t", f"{total:.2f}",
                str(out_path)], capture_output=True)
        return out_path

    filters.append(f"{''.join(parts)}concat=n={idx}:v=0:a=1[out]")
    cmd = [FFMPEG, "-y", *inputs,
           "-filter_complex", ";".join(filters),
           "-map", "[out]", "-t", f"{total:.2f}", "-c:a", "pcm_s16le",
           str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        from core.errors import ProviderError
        raise ProviderError(f"scene audio build failed: {r.stderr[-300:]}")
    return out_path