"""Unit tests for media providers: image (ffmpeg tier), editor, srt."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core.binaries import ffmpeg_path, ffprobe_path
from providers.image import ImageProvider
from apps.editor.editor import (render_clip, concat_clips, merge_audio,
                                burn_subtitles, probe)
from providers.stt.whisper import _fmt_srt


@pytest.fixture()
def img(tmp_path):
    p = ImageProvider()
    out = p.generate("test prompt for gradient", tmp_path / "bg.png",
                     prefer="gradient")
    return out


def test_image_gen_ffmpeg(img):
    assert img.exists()
    assert img.stat().st_size > 1000


def test_image_deterministic(img, tmp_path):
    """Same prompt → same colors (seed-derived)."""
    p = ImageProvider()
    out2 = p.generate("test prompt for gradient", tmp_path / "bg2.png",
                      prefer="gradient")
    assert img.read_bytes() == out2.read_bytes()


def test_render_clip(img, tmp_path):
    clip = render_clip(img, tmp_path / "clip.mp4", 1.0, "zoom-in")
    info = probe(clip)
    assert info["has_video"]
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert abs(info["duration"] - 1.0) < 0.1


def test_render_clip_16_9_landscape(img, tmp_path):
    """Long-form renders 1920x1080; the 9:16 default must stay untouched."""
    clip = render_clip(img, tmp_path / "wide.mp4", 1.0, "zoom-in", ratio="16:9")
    info = probe(clip)
    assert (info["width"], info["height"]) == (1920, 1080)


def test_concat_clips(img, tmp_path):
    c1 = render_clip(img, tmp_path / "c1.mp4", 0.5, "zoom-in")
    c2 = render_clip(img, tmp_path / "c2.mp4", 0.5, "zoom-out")
    joined = concat_clips([c1, c2], tmp_path / "joined.mp4")
    info = probe(joined)
    assert abs(info["duration"] - 1.0) < 0.2


def test_merge_audio_silence(img, tmp_path):
    clip = render_clip(img, tmp_path / "clip.mp4", 0.5)
    out = merge_audio(clip, None, tmp_path / "out.mp4")
    info = probe(out)
    assert info["has_audio"]


def test_bed_gain_tracks_voice_and_slider():
    """Regression: the bed gain must be anchored to the voice, not to an
    absolute attenuation — beds ship ~20 dB below the narration, so an absolute
    mapping left the music inaudible."""
    from apps.editor.audio_levels import bed_gain_db

    assert bed_gain_db(-9.6, 50) == pytest.approx(-9.6 - 8.0)
    # a louder slider means a louder bed
    assert bed_gain_db(-9.6, 100) > bed_gain_db(-9.6, 50) > bed_gain_db(-9.6, 0)
    # the bed never sits above the voice
    assert bed_gain_db(-9.6, 100) < -9.6
    # and never further than ~14 dB under it
    assert bed_gain_db(-9.6, 0) > -9.6 - 15


def test_music_bed_is_audible_in_the_mix(tmp_path):
    """Regression: the bed ended up ~18 dB under the voice and the user
    reported 'no BGM at all'. It must land within a few dB of the narration."""
    import subprocess

    from apps.editor.music import mix_audio

    # narration: 2 s tone, 2 s silence, 2 s tone (a gap the bed should fill).
    # Amplitude matches the real narration files (~-22.6 dBFS RMS).
    parts = tmp_path / "narration.wav"
    subprocess.run(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i",
         "aevalsrc='if(between(t,0,2)+between(t,4,6),"
         "0.1048*sin(2*PI*300*t),0)':s=24000:d=6:c=mono",
         "-ar", "24000", "-ac", "1", str(parts)],
        capture_output=True, check=True)

    # bed at the level the shipped beds have (~-20 dBFS RMS), as mp3 — the mix
    # chain's sidechaincompress only accepts a float-decoded sidechain input.
    raw_bed = tmp_path / "bed_raw.mp3"
    subprocess.run(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i", "sine=frequency=120:duration=6",
         "-ar", "44100", "-ac", "2", "-b:a", "192k", str(raw_bed)],
        capture_output=True, check=True)
    bed = tmp_path / "bed.mp3"
    subprocess.run(
        [ffmpeg_path(), "-y", "-i", str(raw_bed), "-af",
         f"volume={-20.0 - _rms_db(raw_bed):.2f}dB", "-ar", "44100", "-ac", "2",
         "-b:a", "192k", str(bed)],
        capture_output=True, check=True)
    assert abs(_rms_db(bed) + 20.0) < 1.0, "test bed is not at the shipped level"

    out = tmp_path / "mixed.wav"
    mix_audio(parts, bed, out, voice_volume=84, music_volume=49)

    voice = _mean_db(out, 0.3, 1.4)
    gap = _mean_db(out, 2.3, 1.4)
    assert gap > -60, f"bed missing in the gap: {gap:.1f} dBFS"
    # the old absolute mapping put the bed ~18 dB down (reported as "no BGM")
    assert 1.0 < voice - gap < 10.0, f"bed sits {voice - gap:.1f} dB under voice"


def test_bed_is_looped_to_cover_the_whole_video(tmp_path):
    """Regression: the shipped beds are 50-157 s while a long-form video runs
    3-6 min, and the mix did not loop the bed — the music stopped mid-video."""
    import subprocess

    from apps.editor.music import mix_audio

    # voice 30 s with a silent stretch at 10-22 s, so a gap sits well past the
    # bed's length and its level there IS the bed level
    voice = tmp_path / "voice.wav"
    subprocess.run(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i",
         "aevalsrc='if(between(t,0,10)+between(t,22,30),"
         "0.1048*sin(2*PI*300*t),0)':s=24000:d=30:c=mono",
         "-ar", "24000", "-ac", "1", str(voice)],
        capture_output=True, check=True)

    bed = tmp_path / "bed.mp3"          # only 5 s long
    subprocess.run(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i", "sine=frequency=120:duration=5",
         "-af", "volume=-20dB", "-ar", "44100", "-ac", "2", "-b:a", "192k",
         str(bed)], capture_output=True, check=True)

    out = tmp_path / "mixed.wav"
    mix_audio(voice, bed, out, voice_volume=84, music_volume=49)

    dur = float(subprocess.run(
        [ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip())
    # bounded by the narration, not by the looping bed
    assert abs(dur - 30.0) < 0.5, f"mix ran to {dur}s instead of the voice length"
    # music must still be playing 13 s in, long after the 5 s bed ended
    late_gap = _mean_db(out, 13, 4)
    assert late_gap > -60, f"bed stopped before the end: {late_gap:.1f} dBFS"


def test_srt_formatting():
    assert _fmt_srt(0) == "00:00:00,000"
    assert _fmt_srt(61.5) == "00:01:01,500"
    assert _fmt_srt(3661.25) == "01:01:01,250"


def _mean_db(path, ss, t):
    import re
    import subprocess
    r = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-nostats", "-ss", str(ss), "-t", str(t),
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1))


def _rms_db(path):
    import subprocess

    import numpy as np
    raw = subprocess.run(
        [ffmpeg_path(), "-v", "error", "-i", str(path), "-f", "s16le",
         "-ac", "1", "-ar", "8000", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    return 20 * np.log10(max(float(np.sqrt((x ** 2).mean())), 1e-9))


def test_build_scene_audio_does_not_attenuate(tmp_path):
    """Regression: `adelay`+`amix` scaled every clip by 1/N and ramped the
    narration up over the video (36 dB fade-in on a 67-scene long-form), which
    buried the VO under the music bed. Levels must match the source clips."""
    import subprocess

    from apps.editor.media_pipeline import build_scene_audio

    tone = tmp_path / "tone.wav"
    subprocess.run(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ar", "24000", "-ac", "1", str(tone)], capture_output=True, check=True)

    n = 6
    timeline = [{"narration": str(tone), "duration": 1.0} for _ in range(n)]
    timeline.append({"narration": None, "duration": 1.0})  # silent slot
    out = build_scene_audio(timeline, tmp_path / "narration.wav")

    src = _mean_db(tone, 0.1, 0.8)
    first = _mean_db(out, 0.1, 0.8)
    last = _mean_db(out, (n - 1) + 0.1, 0.8)
    gap = _mean_db(out, n + 0.1, 0.8)  # the silent slot

    assert abs(first - src) < 1.0, f"first scene attenuated: {first} vs {src}"
    assert abs(last - src) < 1.0, f"last scene attenuated: {last} vs {src}"
    assert abs(last - first) < 1.0, f"track still ramps: {last - first:.2f} dB"
    assert gap < first - 40, "silent slot is not silent"