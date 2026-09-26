"""YouTube thumbnail: mid-frame of the final render + title overlay."""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.binaries import ffmpeg_path, ffprobe_path, font_path, has_filter


def _esc(t: str) -> str:
    return (t.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\\'").replace("%", "\\%"))


def make_thumbnail(video: Path, out: Path, title: str = "") -> Path:
    """1280x720 JPEG from the video's midpoint, title burned near the bottom.

    ponytail: single-line title with length-based font size; real layout
    (multi-line wrap, brand frame) when thumbnails actually drive CTR work.
    """
    probe = subprocess.run(
        [ffprobe_path(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        mid = float(probe.stdout.strip()) * 0.5
    except ValueError:
        mid = 3.0
    vf = ["scale=1280:720:force_original_aspect_ratio=increase",
          "crop=1280:720"]
    ff = ffmpeg_path("drawtext")
    font = font_path() if has_filter(ff, "drawtext") else None
    if font and title.strip():
        size = 64 if len(title) <= 24 else 48 if len(title) <= 44 else 36
        vf.append(
            "drawtext=fontfile=%s:text='%s':fontsize=%d:fontcolor=white:"
            "borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.74"
            % (font, _esc(title), size))
    r = subprocess.run(
        [ff, "-y", "-ss", f"{mid:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", ",".join(vf), str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"thumbnail failed: {r.stderr[-300:]}")
    return out
