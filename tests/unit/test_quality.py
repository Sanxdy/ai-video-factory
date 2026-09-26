"""Unit tests for quality gate (deterministic checks only — no LLM)."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from apps.quality.quality_gate import QualityGate
from apps.editor.editor import render_clip
from core.binaries import ffmpeg_path


def _still(path: Path, size: str = "1080x1920") -> Path:
    """Textured still, made offline.

    A flat image (what the gradient fallback produces) is pixel-identical frame
    to frame under zoompan, so freezedetect calls it frozen and every clip built
    from one would fail QC for the wrong reason.
    """
    subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi",
                    "-i", f"testsrc2=s={size}:d=1", "-frames:v", "1", str(path)],
                   capture_output=True, check=True)
    return path


@pytest.fixture()
def good_video(tmp_path):
    img = _still(tmp_path / "bg.png")
    # 15s+ clip WITH audio (silence track) to pass min-duration + audio checks
    clip = render_clip(img, tmp_path / "clip.mp4", 15.0, "zoom-in")
    from apps.editor.editor import _add_silence
    return _add_silence(clip, tmp_path / "clip_silent.mp4")


def test_quality_technical_pass(good_video, tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\ntest\n")
    gate = QualityGate(good_video, srt)
    issues = gate.check_technical()
    assert issues == []


def test_quality_wrong_resolution(tmp_path):
    # 512x512 video → must fail resolution check
    import subprocess
    out = tmp_path / "small.mp4"
    subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=512x512:d=0.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   capture_output=True)
    gate = QualityGate(out)
    issues = gate.check_technical()
    assert any("resolution" in i for i in issues)


def test_quality_missing_audio(tmp_path):
    # render_clip produces no audio → has_audio False
    img = _still(tmp_path / "bg.png")
    clip = render_clip(img, tmp_path / "clip.mp4", 15.0, "zoom-in")
    gate = QualityGate(clip)
    issues = gate.check_technical()
    assert any("audio" in i for i in issues)


def test_quality_no_video_path():
    gate = QualityGate(None)
    issues = gate.check_technical()
    assert issues == ["no video path provided"]


def test_quality_rejects_short_long_form(tmp_path):
    """A 16:9 render under 5 minutes must fail QC — the user asked for
    long-form videos of "at least 5 minutes, never less"."""
    import subprocess
    out = tmp_path / "short_wide.mp4"
    subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=1920x1080:d=1",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(out)], capture_output=True)
    issues = QualityGate(out, aspect_ratio="16:9").check_technical()
    assert any("long-form duration" in i for i in issues), issues
    # the same short file is fine as a Short (its own min comes from settings)
    assert not any("long-form duration" in i
                   for i in QualityGate(out).check_technical())


def test_quality_accepts_16_9_long_form(tmp_path):
    """A 1920x1080 project passes when aspect_ratio=16:9, and the Shorts
    duration cap must not apply to it (long-form is legitimately longer)."""
    from apps.editor.editor import _add_silence
    img = _still(tmp_path / "bg.png", "1920x1080")
    clip = render_clip(img, tmp_path / "wide.mp4", 15.0, "zoom-in", ratio="16:9")
    video = _add_silence(clip, tmp_path / "wide_silent.mp4")
    issues = QualityGate(video, aspect_ratio="16:9").check_technical()
    assert not any("resolution" in i for i in issues), issues
    # same file judged as a Short must fail the 1080x1920 rule
    assert any("resolution" in i
               for i in QualityGate(video).check_technical())