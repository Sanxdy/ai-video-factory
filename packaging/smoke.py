#!/usr/bin/env python3
"""Render a synthetic Short through the packaged stack — no API key, no network.

The Fase 1 gate (one real Short with a live LLM key) cannot run on a build machine
that has no key, which is exactly the situation on a fresh Windows or Linux box.
This is the half that can: every bundled-ffmpeg dependency AVF has, driven through
the app's own editor functions.

Two checks are deliberately not "did ffmpeg exit 0":

  * drawtext and libass are asserted to have *painted pixels*, on a uniform black
    background, so a filter that silently renders nothing cannot pass.
  * narration + music exercise aloop, sidechaincompress and amix together — the
    path that differs from the VPS's ffmpeg 4.2 (plan risk #2).

    AVF_DATA_DIR=$(mktemp -d) python3 packaging/smoke.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "core").is_dir():
    sys.path.insert(0, str(ROOT))

SRT = """1
00:00:00,200 --> 00:00:01,600
smoke test caption

"""


def _still(path: Path, colour: tuple[int, int, int]) -> Path:
    from PIL import Image
    Image.new("RGB", (1080, 1920), colour).save(path)
    return path


def _tone(ffmpeg: str, path: Path, freq: int, dur: float) -> Path:
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i",
         f"sine=frequency={freq}:duration={dur}", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True)
    return path


def _frame(ffmpeg: str, video: Path, at: float, out: Path) -> Path:
    subprocess.run(
        [ffmpeg, "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         str(out)], check=True, capture_output=True)
    return out


def _brightest(png: Path) -> int:
    """Peak pixel value — 0 on the uniform backgrounds used below."""
    from PIL import Image
    with Image.open(png) as im:
        return max(im.convert("L").getextrema())


def main() -> int:
    from apps.editor import editor as ed
    from apps.editor.thumbnail import make_thumbnail

    tmp = Path(tempfile.mkdtemp(prefix="avf-smoke-"))
    ok: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        if not condition:
            raise SystemExit(f"FAIL {label}{' — ' + detail if detail else ''}")
        ok.append(f"{label}{' (' + detail + ')' if detail else ''}")

    # A bundle that silently reaches for a host ffmpeg is the failure this whole
    # exercise exists to prevent: it renders fine on the build machine and dies on
    # a user's machine that has none. Only asserted when one is actually shipped,
    # so a source checkout still passes.
    from core.config import ROOT
    vendored = ROOT / "vendor" / f"ffmpeg{'.exe' if os.name == 'nt' else ''}"
    if vendored.is_file():
        check("uses the bundled ffmpeg", Path(ed.FFMPEG) == vendored, ed.FFMPEG)

    # 1. stills → motion clips → concat (libx264, scale/crop, zoompan)
    black = _still(tmp / "black.png", (0, 0, 0))
    shot = _still(tmp / "shot.png", (18, 22, 40))
    clips = [ed.render_clip(shot, tmp / f"clip{i}.mp4", 2.0, "zoom-in", "", "9:16")
             for i in range(2)]
    body = ed.concat_clips(clips, tmp / "body.mp4")
    info = ed.probe(body)
    check("render+concat", info["has_video"] and info["width"] == 1080
          and info["height"] == 1920,
          f"{info['width']}x{info['height']} {info['duration']:.2f}s")
    check("duration", abs((info["duration"] or 0) - 4.0) < 0.5,
          f"{info['duration']:.2f}s for 2x2s")

    # 2. drawtext actually paints — a uniform still can only gain pixels from the
    #    caption, so "ffmpeg exited 0" is not what is being asserted here.
    plain = ed.render_clip(black, tmp / "plain.mp4", 2.0, "zoom-in", "", "9:16")
    check("no overlay = no pixels", _brightest(_frame(ed.FFMPEG, plain, 1.0,
                                                      tmp / "plain.png")) < 40)
    titled = ed.render_clip(black, tmp / "titled.mp4", 2.0, "zoom-in",
                            "AVF SMOKE", "9:16")
    check("drawtext paints", _brightest(_frame(ed.FFMPEG, titled, 1.0,
                                               tmp / "titled.png")) > 200)

    # 3. narration + music bed → aloop, sidechaincompress, amix, apad
    voice = _tone(ed.FFMPEG, tmp / "voice.wav", 220, 4.0)
    bed = _tone(ed.FFMPEG, tmp / "bed.wav", 110, 2.0)
    mixed = ed.merge_audio(body, voice, tmp / "mixed.mp4", music=bed)
    check("merge_audio", ed.probe(mixed)["has_audio"])

    # 4. libass paints, same uniform-background trick as drawtext
    srt = tmp / "subs.srt"
    srt.write_text(SRT)
    check("libass paints", _brightest(_frame(
        ed.FFMPEG, ed.burn_subtitles(plain, srt, tmp / "burned.mp4"), 1.0,
        tmp / "burned.png")) > 200)

    # 5. the real order: mix → subtitles → loudnorm. loudness_normalize carries
    #    audio through an -af, so it needs a track to normalize — merge_audio is
    #    what always supplies one (narration, music, or _add_silence).
    final = ed.loudness_normalize(
        ed.burn_subtitles(mixed, srt, tmp / "mixed_subs.mp4"), tmp / "final.mp4")
    fin = ed.probe(final)
    check("loudnorm keeps video+audio", fin["has_video"] and fin["has_audio"])
    check("loudnorm keeps duration", abs((fin["duration"] or 0) - 4.0) < 0.5,
          f"{fin['duration']:.2f}s")
    thumb = make_thumbnail(final, tmp / "thumb.jpg", "AVF SMOKE")
    check("thumbnail", thumb.exists() and thumb.stat().st_size > 1000,
          f"{thumb.stat().st_size / 1000:.0f} KB")

    print(f"ffmpeg {ed.FFMPEG}")
    for line in ok:
        print(f"  ok  {line}")
    print(f"smoke ok — {len(ok)} checks, output in {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
