"""FFmpeg editor (spec §18, §15) — stills + Ken Burns motion → video.

Pipeline per scene: image → (zoom/pan via scale+crop) → silence/voice clip
→ concat all scenes → merge audio (narration + optional music) → subtitles
→ loudness normalize → final H.264 MP4 1080x1920 30fps.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


from core.binaries import ffmpeg_path, ffprobe_path, font_path
from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("editor")

W, H, FPS = 1080, 1920, 30        # 9:16 default — kept for existing callers
RATIOS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}


def dims(ratio: str | None = None) -> tuple[int, int]:
    """Frame size for an aspect ratio. Unknown/None → 9:16 (Shorts)."""
    return RATIOS.get(ratio or "9:16", RATIOS["9:16"])


def resolution(ratio: str | None = None) -> str:
    """Frame size as 'WxH' — the form ImageProvider/stock providers take."""
    w, h = dims(ratio)
    return f"{w}x{h}"


MOTION_ZOOM = "zoompan=z='1+0.08*on/{frames}':d={frames}:s={w}x{h}:fps={fps}"
# pan via zoompan x/y linear in on (output frame counter, 0..frames)
MOTION_PAN_LR = "zoompan=z=1.1:x='(iw-iw/zoom)*(on/{frames})':y='(ih-ih/zoom)/2':d={frames}:s={w}x{h}:fps={fps}"
MOTION_PAN_RL = "zoompan=z=1.1:x='(iw-iw/zoom)*(1-on/{frames})':y='(ih-ih/zoom)/2':d={frames}:s={w}x{h}:fps={fps}"
MOTION_PAN_UD = "zoompan=z=1.1:x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*(on/{frames})':d={frames}:s={w}x{h}:fps={fps}"
MOTION_PAN_DU = "zoompan=z=1.1:x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*(1-on/{frames})':d={frames}:s={w}x{h}:fps={fps}"


def _motion_filter(motion: str, dur: float, wh: tuple[int, int] = (W, H)) -> str:
    w, h = wh
    frames = int(dur * FPS)
    motion = (motion or "zoom-in").lower()
    if "zoom" in motion:
        return MOTION_ZOOM.format(frames=frames, dur=dur, w=w, h=h, fps=FPS)
    if "left" in motion:
        return MOTION_PAN_LR.format(frames=frames, dur=dur, w=w, h=h, fps=FPS)
    if "right" in motion:
        return MOTION_PAN_RL.format(frames=frames, dur=dur, w=w, h=h, fps=FPS)
    if "up" in motion:
        return MOTION_PAN_UD.format(frames=frames, dur=dur, w=w, h=h, fps=FPS)
    if "down" in motion or "bottom" in motion:
        return MOTION_PAN_DU.format(frames=frames, dur=dur, w=w, h=h, fps=FPS)
    # default gentle zoom
    return MOTION_ZOOM.format(frames=frames, dur=dur, w=w, h=h, fps=FPS)


def image_clip_cmd(image: Path, out: Path, dur: float, motion: str = "zoom-in",
                   overlay_text: str = "", ratio: str | None = None) -> list[str]:
    """Build ffmpeg cmd turning a still image into a motion clip with optional text."""
    w, h = dims(ratio)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
    vf += _motion_filter(motion, dur, (w, h))
    if overlay_text:
        # bold centered caption, safe bottom margin — both scale with frame height
        # so 16:9 gets the same visual weight as 9:16 (90px/-500px @1920h)
        from apps.editor.thumbnail import _esc
        fs = max(28, round(h * 0.047))
        ff = (f",drawtext=text='{_esc(overlay_text)}':fontsize={fs}:fontcolor=white:"
              f"borderw=6:bordercolor=black:x=(w-text_w)/2:y=h-{round(h * 0.26)}")
        font = font_path()
        if font:
            ff += f":fontfile={_esc(font)}"
        vf += ff
    # ponytail: feed the image ONCE — zoompan d={frames} emits the whole clip in one burst,
    # so `on` counts 1..N continuously (with -loop 1 it resets per input frame → motion stalls).
    # Ceiling: no crossfade between scenes; add xfade when scenes need transitions.
    return [
        FFMPEG, "-y", "-i", str(image),
        "-vf", vf, "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(out),
    ]


FFMPEG = ffmpeg_path()
FFPROBE = ffprobe_path()


def run_ffmpeg(cmd: list[str], timeout: int = 900) -> None:
    """Run ffmpeg, raise ProviderError on failure.

    Default 900s (not 300): long-form renders are ~6 min of video and a single
    pass can exceed 5 minutes on the 2-core VPS.
    """
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ProviderError(f"ffmpeg failed: {r.stderr[-500:]}")


def video_clip(src: Path, out: Path, dur: float, text_overlay: str = "",
               ratio: str | None = None) -> Path:
    """Normalize stock footage to the frame size, loop-fill to duration, mute."""
    w, h = dims(ratio)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h},fps={FPS}")
    if text_overlay:
        from apps.editor.thumbnail import _esc
        fs = max(28, round(h * 0.042))
        ff = (f",drawtext=text='{_esc(text_overlay)}':fontsize={fs}:fontcolor=white:"
              f"borderw=5:bordercolor=black:x=(w-text_w)/2:y=h-{round(h * 0.26)}")
        font = font_path()
        if font:
            ff += f":fontfile={_esc(font)}"
        vf += ff
    run_ffmpeg([
        FFMPEG, "-y", "-stream_loop", "-1", "-i", str(src),
        "-t", f"{dur:.2f}",
        "-vf", vf,
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def render_clip(image: Path, out: Path, dur: float, motion: str = "zoom-in",
                text_overlay: str = "", ratio: str | None = None) -> Path:
    """Render a single animated clip from a still. Returns output path."""
    cmd = image_clip_cmd(image, out, dur, motion, overlay_text=text_overlay,
                         ratio=ratio)
    run_ffmpeg(cmd)
    return out


def concat_clips(clips: list[Path], out: Path) -> Path:
    """Concat pre-rendered clips (same codec/res) → single video."""
    listfile = out.parent / "concat.txt"

    def _q(p: Path) -> str:
        # concat demuxer quoting is shell-style — escape embedded '
        return str(p.resolve()).replace("'", "'\\''")

    listfile.write_text("".join(f"file '{_q(c)}'\n" for c in clips))
    run_ffmpeg([FFMPEG, "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile), "-c", "copy", str(out)])
    return out


def merge_audio(video: Path, narration: Path | None, out: Path,
                music: Path | None = None, music_db: float | None = None,
                voice_volume: int | None = None, music_volume: int | None = None) -> Path:
    """Mux narration (+ ducked music) under video. Returns output path."""
    from apps.editor.audio_levels import (vol_to_db, bed_gain_db,
                                          default_voice_volume,
                                          default_music_volume)
    if voice_volume is None:
        voice_volume = default_voice_volume()
    if music_volume is None:
        music_volume = default_music_volume()

    voice_db = vol_to_db(voice_volume)

    if music_db is not None:
        music_db_val = float(music_db)
    else:
        music_db_val = bed_gain_db(voice_db, music_volume)

    inputs = [FFMPEG, "-y", "-i", str(video)]
    filters: list[str] = []
    n = 1
    if narration and narration.exists():
        inputs += ["-i", str(narration)]
        filters.append(f"[{n}:a]aresample=44100,volume={voice_db}dB[voice]")
        n += 1
    if music and music.exists():
        inputs += ["-i", str(music)]
        # loop + duck under voice via sidechaincompress
        filters.append(
            f"[{n}:a]aloop=loop=-1:size=2e9,"
            f"volume={music_db_val}dB[bg]"
        )
        n += 1

    if not filters:
        # no narration, no music → attach silence so video has an audio track
        return _add_silence(video, out)

    if narration and music:
        # sidechain ducking: bg ducks when voice present — [voice] is consumed
        # twice, so split it first (a bare label can only feed one filter)
        filters += [
            "[voice]asplit=2[vc][vs]",
            "[bg][vc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[ducked]",
            "[ducked][vs]amix=inputs=2:duration=first:dropout_transition=0[aout]",
        ]
    else:
        src = "[voice]" if narration else "[bg]"
        filters.append(f"{src}apad[aout]")

    cmd = inputs + ["-filter_complex", ";".join(filters),
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "44100",
                    "-shortest", str(out)]
    run_ffmpeg(cmd)
    return out


def _srt_style(w: int, h: int) -> str:
    """force_style for the plain-SRT (classic) path.

    libass PlayRes is 384x288 by default — all sizes are in that space!
    Convert desired pixel values: value_288 = px / h * 288
    Target: font ≈5% of width (54px @1080w), margin ≈10% of height (192px @1920h)

    Proportional to the frame, so the subtitle-preview endpoint can call this
    with its own (smaller) frame and get the style a real render would use.
    """
    fontsize = max(6, round(min(w, h) * 0.05 / h * 288))    # ≈8 units
    marginv = max(10, round(h * 0.10 / h * 288))            # ≈29 units
    return (f"FontName=DejaVu Sans,FontSize={fontsize},MarginV={marginv},"
            f"Alignment=2,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=1,Shadow=0")


def burn_subtitles(video: Path, srt: Path, out: Path) -> Path:
    """Burn ASS/SRT subtitles into video, size proportional to aspect.

    Font/margin scale with the frame so 9:16 Shorts get large readable
    captions (~6.2% of width) and 16:9 gets normal-sized ones.
    """
    info = probe(video)
    w, h = info["width"] or 1080, info["height"] or 1920
    style = _srt_style(w, h)
    # absolute path — relative paths get mangled by the -vf colon parser
    # level-1 filter escaping: \ : , ' are all filter-argument specials
    def esc(p: Path) -> str:
        return "".join("\\" + c if c in "\\:,+'" else c for c in str(p.resolve()))

    rel = esc(srt)
    # drawtext takes an explicit fontfile, libass does not: it asks fontconfig for
    # the family in the style. Naming the directory the bundled font lives in keeps
    # the render on the intended typeface instead of whatever the host installed.
    font = font_path()
    fontsdir = f":fontsdir={esc(Path(font).parent)}" if font else ""
    # ASS files carry their own styles — force_style would fight them
    if srt.suffix == ".ass":
        vf = f"subtitles={rel}{fontsdir}"
    else:
        # quotes are for ffmpeg's own filter parser (protects commas), not a shell
        vf = f"subtitles={rel}{fontsdir}:force_style='{style}'"
    run_ffmpeg([
        FFMPEG, "-y", "-i", str(video), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", str(out),
    ], timeout=600)
    return out


def _measure_loudness(src: Path) -> dict:
    """One-pass loudnorm measurement of a file (audio only, no output)."""
    r = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(src),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=900)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    return json.loads(m.group(0)) if m else {}


def linear_loudnorm_filter(src: Path, target_i: float = -14.0,
                           tp: float = -1.5) -> str:
    """loudnorm filter that applies ONE fixed gain instead of riding gain.

    Dynamic loudnorm lifts quiet passages toward loud ones. With a music bed
    under sparse narration that raises the bed in every gap until it is as
    loud as the voice — measured on project 290, bed-only intro came out at
    -13.8 LUFS against -14.6 LUFS where the voice plays, i.e. the BGM was
    LOUDER than the VO. `linear=true` applies a single gain, so the voice/bed
    ratio set by the mixer survives normalization.
    """
    d = _measure_loudness(src)
    if not d:
        return f"loudnorm=I={target_i}:TP={tp}"
    return (f"loudnorm=I={target_i}:TP={tp}:linear=true"
            f":measured_I={d['input_i']}:measured_TP={d['input_tp']}"
            f":measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}")


def normalize_linear(src: Path, out: Path, target_i: float = -14.0,
                     tp: float = -1.5) -> Path:
    """Two-pass linear loudness normalization (measure, then apply one gain)."""
    r = subprocess.run(
        [FFMPEG, "-y", "-i", str(src),
         "-af", linear_loudnorm_filter(src, target_i, tp),
         "-ar", "44100", str(out)],
        capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise ProviderError(f"ffmpeg normalize failed: {r.stderr[-300:]}")
    return out


def loudness_normalize(video: Path, out: Path) -> Path:
    """EBU R128 loudness normalization, target -14 LUFS (YouTube standard).

    Linear gain, not dynamic: see linear_loudnorm_filter for why dynamic mode
    makes the music bed louder than the narration.

    `-ar 44100` is load-bearing, not decoration: loudnorm measures at 192 kHz
    internally, and without an explicit rate the encoder takes whatever the
    filter graph negotiates, so final.mp4 came out at 96000 Hz against a 44100
    Hz source. Pinning it here keeps the shipped file at the rate the rest of
    the chain uses.
    """
    run_ffmpeg([
        FFMPEG, "-y", "-i", str(video),
        "-af", linear_loudnorm_filter(video, -14.0, -1.5),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(out),
    ])
    return out


def _add_silence(video: Path, out: Path) -> Path:
    """Attach a silent AAC track matching video duration."""
    dur = probe(video)["duration"]
    run_ffmpeg([
        FFMPEG, "-y", "-i", str(video),
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", f"{dur:.2f}", "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
        "-shortest", str(out),
    ])
    return out


def _parse_fps(rate: str | None) -> float | None:
    """'30000/1001'-style fraction → float, without eval()."""
    if not rate:
        return None
    try:
        num, _, den = rate.partition("/")
        return round(int(num) / int(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> dict:
    """ffprobe → {duration, width, height, has_video, has_audio, fps}."""
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise ProviderError(f"ffprobe failed: {r.stderr[:200]}")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video"), None)
    a = next((s for s in streams if s["codec_type"] == "audio"), None)
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "width": v.get("width") if v else None,
        "height": v.get("height") if v else None,
        "fps": _parse_fps(v.get("avg_frame_rate") if v else None),
        "has_video": v is not None,
        "has_audio": a is not None,
        "size": Path(path).stat().st_size,
    }