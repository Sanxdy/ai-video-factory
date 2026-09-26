"""Re-render the audio of already-rendered videos after the amix fix.

Reuses each project's video_silent.mp4 (visuals are unaffected) and replays the
pipeline's post-concat steps: build narration -> mix bed -> merge -> subtitles
-> watermark -> loudness normalize. Music-bed choice mirrors stage_edit exactly.

Usage: python scripts/reaudio_pending.py 285 287 288 289
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.editor.audio_levels import get_volumes
from apps.editor.editor import burn_subtitles, loudness_normalize, merge_audio, probe
from apps.editor.media_pipeline import build_scene_audio
from apps.editor.music import find_music, mix_audio
from apps.editor import watermark as wm_mod
from apps.orchestrator.pipeline import _mood_for_playlist
from core.config import config
from core.database import Database
from core.settings import get_setting

ROOT = Path(config.root)


def mean_db(path, ss, t):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(ss), "-t", str(t),
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else None


def bed_for(pid: int) -> Path | None:
    """Same selection stage_edit makes."""
    ov = get_setting(f"project.{pid}.music")
    if ov:
        return ROOT / "content" / "music" / ov
    mood = _mood_for_playlist(get_setting(f"project.{pid}.playlist") or "")
    cand = ROOT / "content" / "music" / f"{mood}.mp3"
    return cand if cand.exists() else None


def reaudio(pid: int, db: Database) -> None:
    R = ROOT / "content" / "rendered" / f"project-{pid:05d}"
    PDIR = ROOT / "content" / "projects" / f"project-{pid:05d}"
    silent = R / "video_silent.mp4"
    assert silent.exists(), f"{pid}: no video_silent.mp4"

    proj = db.get("projects", pid) or {}
    ratio = proj.get("aspect_ratio") or "9:16"

    man = json.loads((R / "manifest.json").read_text())
    timeline = man["timeline"]

    # drop the stale (attenuated) track so build_scene_audio runs again
    (R / "narration.wav").unlink(missing_ok=True)

    vv, mv = get_volumes(get_setting)
    voice = build_scene_audio(timeline, R / "narration.wav")
    bed = bed_for(pid) or find_music()
    print(f"[{pid}] ratio={ratio} bed={bed.name if bed else None}", flush=True)
    if bed:
        voice = mix_audio(voice, bed, R / "narration_mixed.wav",
                          voice_volume=vv, music_volume=mv)

    mixed = merge_audio(silent, voice, R / "video_mixed.mp4",
                        voice_volume=vv, music_volume=mv)
    subs = PDIR / "subtitles.ass"
    if not subs.exists():
        subs = PDIR / "subtitles.srt"
    if subs.exists():
        mixed = burn_subtitles(mixed, subs, R / "video_subs.mp4")
    mixed = wm_mod.apply_watermark(mixed, R / "video_wm.mp4", db, ratio=ratio)
    final = loudness_normalize(mixed, R / "final.mp4")

    info = probe(final)
    dur = info["duration"]
    prof = [mean_db(final, i * 10, 10) for i in range(max(2, int(dur // 10)))]
    prof = [v for v in prof if v is not None]
    ramp = prof[-1] - prof[0]
    print(f"[{pid}] {dur:.1f}s {info['width']}x{info['height']} "
          f"ramp={ramp:+.1f} dB  {[round(v, 1) for v in prof]}", flush=True)
    assert abs(ramp) < 5.0, f"{pid}: audio still ramps {ramp:.1f} dB"


def main():
    pids = [int(a) for a in sys.argv[1:]]
    assert pids, "pass project ids"
    db = Database(str(ROOT / "database" / "factory.db"))
    for pid in pids:
        reaudio(pid, db)
    print("DONE", pids)


if __name__ == "__main__":
    main()
