"""Subtitle presets (ASS/libass) — Norraclip-inspired caption styles.

classic keeps the plain SRT path; the rest render as .ass burned by libass
(ffmpeg-full build carries it). Word timings are derived from scene duration
weighted by word length — no STT pass needed.
"""
from __future__ import annotations

from pathlib import Path

PRESETS: dict[str, dict] = {
    "classic": {"label": "Classic — clean white outline"},
    "bangers": {"label": "Bangers — bold yellow pop", "mode": "word",
                "font": "Impact", "size": 96, "primary": "&H0033FFFF",
                "secondary": "&H00FFFFFF", "outline": "&H00000000",
                "outline_w": 5, "upper": True, "margin_v": 420, "bold": 1},
    "rapid": {"label": "Rapid — one word at a time", "mode": "word",
              "font": "Arial Black", "size": 84, "primary": "&H00FFFFFF",
              "secondary": "&H00FFFFFF", "outline": "&H00000000",
              "outline_w": 4, "upper": True, "margin_v": 480, "bold": 1},
    "mozi": {"label": "Mozi — pink word box", "mode": "word",
             "font": "Arial Black", "size": 76, "primary": "&H00FFFFFF",
             "secondary": "&H00FFFFFF", "outline": "&H00B24DFF",
             "outline_w": 14, "upper": True, "margin_v": 460, "bold": 1,
             "border_style": 3},
    "karaoke": {"label": "Karaoke — lime highlight sweep", "mode": "karaoke",
                "font": "Impact", "size": 84, "primary": "&H0000FEC7",
                "secondary": "&H00FFFFFF", "outline": "&H00000000",
                "outline_w": 4, "upper": True, "margin_v": 440, "bold": 1},
    "elegant": {"label": "Elegant — minimal serif", "mode": "line",
                "font": "Georgia", "size": 62, "primary": "&H00FFFFFF",
                "secondary": "&H00FFFFFF", "outline": "&H00000000",
                "outline_w": 1, "upper": False, "margin_v": 300, "bold": 0,
                "shadow": 1},
}

DEFAULT_PRESET = "classic"


def subtitle_preset() -> str:
    from core.settings import get_setting
    p = get_setting("subtitle.preset") or DEFAULT_PRESET
    return p if p in PRESETS else DEFAULT_PRESET


def _ts(seconds: float) -> str:
    s = max(0.0, seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def _word_timings(text: str, start: float, dur: float) -> list[tuple[str, float, float]]:
    words = text.split()
    weights = [len(w) + 1 for w in words]
    total = sum(weights) or 1
    out, cursor = [], start
    for w, wt in zip(words, weights):
        d = dur * wt / total
        out.append((w, cursor, cursor + d))
        cursor += d
    return out


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def _wav_dur(path: str | None) -> float | None:
    """Real duration of a per-scene TTS wav (None if missing/invalid). Ground
    truth for caption windows — LLM scene-duration estimates run ±30% off and
    make captions overlap following VO (project 18/19 sync bug)."""
    if not path or not Path(path).exists():
        return None
    try:
        import wave
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return None


def build_ass(scenes: list[dict], audio_paths: list[str | None],
              out_path: Path, preset: str | None = None) -> Path:
    """Write an ASS subtitle file for the scene timeline."""
    p = PRESETS[preset or subtitle_preset()]
    events: list[str] = []
    cursor = 0.0
    for sc, ap in zip(scenes, audio_paths):
        text = (sc.get("narration") or "").strip()
        # caption window = real spoken length (caption hides when VO ends —
        # NEVER extend past the voice into the next scene's narration).
        real = _wav_dur(ap)
        dur = max(float(real if real else sc["duration"]), 0.25)
        # scene slot (cursor advance) = same padded duration the audio uses
        # (voice + 0.25s pad + any countdown pause), so captions start exactly
        # when the VO starts.
        slot = (max(float(sc.get("duration", 4.0)),
                    float(real if real else sc.get("duration", 4.0))) + 0.25
                + float(sc.get("pause_after") or 0))
        if text and ap:
            t = _clean(text.upper() if p.get("upper") else text)
            mode = p["mode"]
            if mode == "word":
                for w, s, e in _word_timings(t, cursor, dur):
                    events.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{w}")
            elif mode == "karaoke":
                # chunk the line: one event shows at most ~2 wrapped rows,
                # otherwise a full scene sentence wraps to 5+ lines and buries
                # half the frame. At size 84 Impact, ~2 words fit per row on
                # 1080px width, so 4 words = max 2 rows.
                CHUNK = 4
                spans = _word_timings(t, cursor, dur)
                for i in range(0, len(spans), CHUNK):
                    grp = spans[i:i + CHUNK]
                    tags = " ".join(r"{\k%d}%s" % (round((e - s) * 100), w)
                                    for w, s, e in grp)
                    events.append(f"Dialogue: 0,{_ts(grp[0][1])},{_ts(grp[-1][2])},"
                                  f"Cap,,0,0,0,,{tags}")
            else:
                # line mode: cap displayed text at ~2 rows too — split long
                # narration into sequential ~6-word chunks instead of one blob
                words = t.split()
                for i in range(0, len(words), 8):
                    chunk = " ".join(words[i:i + 8])
                    frac_s = sum(len(w) + 1 for w in words[:i]) / max(1, sum(len(w) + 1 for w in words))
                    frac_e = sum(len(w) + 1 for w in words[:i + 8]) / max(1, sum(len(w) + 1 for w in words))
                    s = cursor + dur * frac_s
                    e = cursor + dur * min(1.0, frac_e)
                    events.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Cap,,0,0,0,,{chunk}")
        # advance by the padded scene slot (matches build_scene_audio audio
        # placement) — NOT the raw voice length, so the next caption starts
        # exactly when the next VO starts.
        cursor += slot

    kw = {**p, "border_style": p.get("border_style", 1), "shadow": p.get("shadow", 0)}
    # 23 fields exactly: ..., Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY,
    # Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL/R/V, Encoding
    style = (
        "Style: Cap,{font},{size},{primary},{secondary},{outline},{outline},"
        "{bold},0,0,0,100,100,0,0,{border_style},{outline_w},{shadow},2,90,90,{margin_v},1"
    ).format(**kw)

    ass = "\n".join([
        "[Script Info]", "ScriptType: v4.00+",
        "PlayResX: 1080", "PlayResY: 1920",
        "ScaledBorderAndShadow: yes", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginE, Effect, Text",
        *events, "",
    ])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(ass, encoding="utf-8")
    return out_path


# The sentence the previews use. Ordinary English with a long word and a short
# one, so word-timing spread and the wrap point are both visible.
PREVIEW_TEXT = "This is how your captions will look"


def render_preview(preset: str, out: Path,
                   width: int = 720, height: int = 1280) -> Path:
    """Render `preset` the way a render would — a PNG, or a GIF when the preset
    is one whose caption changes word by word.

    Not a mock-up. It writes the same ASS a render would and burns it with the
    same ffmpeg filter the encoder uses, so the difference between two presets
    on screen here is the difference the user will actually get. A preview drawn
    in HTML would drift from libass the first time a preset changed.

    Animated for word- and karaoke-mode presets. Those show a different word
    every fraction of a second, so a single frame proves almost nothing — four
    of the six presets all rendered as "THIS", which is what the picker was
    asking the user to choose between. One frame per event (two per event when
    there are fewer than four, so a karaoke chunk is caught part-way through its
    own sweep) is the least that shows the rhythm. Line-mode presets show the
    whole sentence in every frame, so a still is complete for them.

    Cropped to the caption rather than to the bottom-40% band. The band left
    ~75% of each preview as empty grey — it read as a broken image, and it was
    the reason a card had to be 556px wide to be legible at all. The crop is the
    union bounding box across frames (so the GIF does not jump between words),
    padded, and floored at a minimum size so a one-word frame still reads as a
    preview rather than a stamp.

    720 wide: sizes are proportional to the frame (_srt_style, and the fixed
    PlayRes in build_ass), so the style shown is unchanged — only its
    resolution, which a ~96 CSS px thumbnail on a 2x display still downsamples
    from.
    """
    import re as _re
    import shutil
    from apps.editor.editor import _srt_style, run_ffmpeg
    from core.binaries import ffmpeg_path, font_path

    if preset not in PRESETS:
        raise ValueError(f"unknown subtitle preset: {preset}")

    # Absolute path with the filter-argument escapes; relative paths get mangled
    # by the -vf colon parser. Same escaping the burn-in step uses.
    def esc(p: Path) -> str:
        return "".join("\\" + c if c in "\\:,+'" else c for c in str(p.resolve()))

    font = font_path()
    fontsdir = f":fontsdir={esc(Path(font).parent)}" if font else ""

    work = out.parent
    work.mkdir(parents=True, exist_ok=True)

    # classic never goes through build_ass in production — it is an SRT burned
    # with a force_style. Previewing it through the ASS path would show a style
    # no render ever produces, so mirror the real one.
    if preset == "classic":
        srt = work / "classic.srt"
        srt.write_text(f"1\n00:00:00,000 --> 00:00:04,000\n{PREVIEW_TEXT}\n",
                       encoding="utf-8")
        events = [(0.0, 4.0)]
        vf = (f"subtitles={esc(srt)}{fontsdir}"
              f":force_style='{_srt_style(width, height)}'")
    else:
        # build_ass skips any scene without a VO path — that is how a caption
        # stays inside its own voice. The preview has no audio, so hand it a
        # path that does not exist: _wav_dur returns None and the duration
        # falls back to the scene's own.
        ass = build_ass([{"narration": PREVIEW_TEXT, "duration": 4.0}], ["preview"],
                        work / f"{preset}.ass", preset)
        events = [(_ts_parse(s), _ts_parse(e)) for s, e in _re.findall(
            r"^Dialogue: [^,]*,([^,]*),([^,]*),", ass.read_text(), _re.M)]
        if not events:
            raise RuntimeError(f"{preset}: no dialogue events were generated")
        vf = f"subtitles={esc(ass)}{fontsdir}"

    # Word/karaoke captions change inside one sentence, so they animate; a line
    # caption is the same sentence throughout, so one frame is complete — unless
    # it has several events (long text chunks), which do differ.
    mode = PRESETS[preset].get("mode")
    if mode in ("word", "karaoke") or len(events) > 1:
        if len(events) >= 4:
            times = [s + (e - s) * 0.5 for s, e in events]
        else:
            times = [t for s, e in events
                     for t in (s + (e - s) * 0.3, s + (e - s) * 0.7)]
        times = times[:8]
    else:
        s, e = events[0]
        times = [s + (e - s) * 0.5]

    # One ffmpeg run for every frame: a constant-colour input at 25 fps has
    # frame n at t = n/25, so selecting frame numbers is exact and costs one
    # filter init instead of one process per frame.
    FPS = 25
    ns = sorted({max(0, round(t * FPS)) for t in times})
    frames_dir = work / f"frames-{preset}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        ffmpeg_path(), "-y",
        # Flat mid-grey: the caption is the subject, and footage behind it would
        # compete with the one thing the user is trying to judge.
        "-f", "lavfi", "-i",
        f"color=c=0x2A2A2A:s={width}x{height}:d={max(e for _, e in events) + 0.5:.3f}",
        "-vf", f"{vf},select='{'+'.join(f'eq(n,{n})' for n in ns)}'",
        "-fps_mode", "passthrough", "-start_number", "0",
        str(frames_dir / "frame%03d.png"),
    ], timeout=300)
    frames = sorted(frames_dir.glob("frame*.png"))
    if not frames:
        raise RuntimeError(f"{preset}: no preview frames were rendered")

    # Union bounding box over all frames — per-frame boxes would make the GIF
    # jump around as words of different widths came and went.
    import numpy as _np
    from PIL import Image
    bg = _np.array([0x2A, 0x2A, 0x2A])
    box = None
    for f in frames:
        a = _np.asarray(Image.open(f).convert("RGB"), dtype=int)
        ys, xs = _np.nonzero((_np.abs(a - bg) > 14).any(axis=2))
        if not len(ys):
            continue
        b = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                     max(box[2], b[2]), max(box[3], b[3]))
    box = box or (0, 0, width, height)

    PAD, MIN_W, MIN_H = 26, 360, 120
    x0 = max(0, box[0] - PAD)
    y0 = max(0, box[1] - PAD)
    x1 = min(width, box[2] + PAD)
    y1 = min(height, box[3] + PAD)
    # Floor the size, centred on the caption and clamped to the frame, so a
    # single short word does not become a postage stamp.
    def _floor(lo: int, hi: int, minimum: int, limit: int) -> tuple[int, int]:
        if hi - lo >= minimum:
            return lo, hi
        c = (lo + hi) // 2
        lo = max(0, min(c - minimum // 2, limit - minimum))
        return lo, lo + minimum
    x0, x1 = _floor(x0, x1, MIN_W, width)
    y0, y1 = _floor(y0, y1, MIN_H, height)

    imgs = [Image.open(f).convert("RGB").crop((x0, y0, x1, y1)) for f in frames]
    try:
        if len(imgs) > 1:
            # 400 ms per frame: long enough to read a word, short enough that
            # the loop reads as the caption's rhythm rather than a slideshow.
            imgs[0].save(out, save_all=True, append_images=imgs[1:],
                         duration=400, loop=0, optimize=True)
        else:
            imgs[0].save(out)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
    return out


def _ts_parse(ts: str) -> float:
    h, m, s = ts.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)
