"""ASS subtitle builder: modes emit Dialogue lines, timings tile the scene."""
from apps.editor.subtitles import PRESETS, _word_timings, build_ass

SCENES = [
    {"narration": "Two words here", "duration": 4.0},
    {"narration": "", "duration": 2.0},  # silent scene → no events
]
AUDIO = ["a.wav", None]


def test_word_mode_one_event_per_word(tmp_path):
    out = build_ass(SCENES, AUDIO, tmp_path / "s.ass", preset="bangers")
    text = out.read_text()
    events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 3  # words of scene 1 only
    assert all(",Cap," in e for e in events)
    assert "TWO" in events[0] and "HERE" in events[-1]  # upper


def test_karaoke_and_line_modes(tmp_path):
    kar = build_ass(SCENES, AUDIO, tmp_path / "k.ass", preset="karaoke")
    assert sum(1 for l in kar.read_text().splitlines() if l.startswith("Dialogue:")) == 1
    assert r"{\k" in kar.read_text()

    lin = build_ass(SCENES, AUDIO, tmp_path / "l.ass", preset="elegant")
    ev = [l for l in lin.read_text().splitlines() if l.startswith("Dialogue:")]
    assert len(ev) == 1 and "Two words here" in ev[0]  # not uppercased


def test_style_line_matches_preset(tmp_path):
    out = build_ass(SCENES, AUDIO, tmp_path / "s.ass", preset="mozi")
    style = next(l for l in out.read_text().splitlines() if l.startswith("Style:"))
    assert "Arial Black" in style and ",3," in style  # border_style=3 box
    assert "&H00B24DFF" in style  # pink box colour


def test_word_timings_tile_duration():
    spans = _word_timings("aa bbbb c", 10.0, 6.0)
    assert abs(spans[0][1] - 10.0) < 1e-9
    assert abs(spans[-1][2] - 16.0) < 1e-9
    assert all(e >= s for _, s, e in spans)


def test_presets_catalog():
    assert "classic" in PRESETS and "mode" not in PRESETS["classic"]
    assert {p["mode"] for p in PRESETS.values() if "mode" in p} <= {"word", "karaoke", "line"}


def test_preview_frame_has_the_caption_in_it(tmp_path):
    """The preview must be a real frame with text in it.

    Word mode emits one event per word, so a fixed timestamp lands between
    events for some presets and renders an empty frame — which is exactly the
    bug the timestamp readback exists to prevent. Assert on pixels, not on the
    file existing: a blank grey PNG passes a file-exists check.
    """
    from apps.editor.subtitles import render_preview
    png = render_preview("bangers", tmp_path / "b.png")
    from PIL import Image
    img = Image.open(png).convert("L")
    # Cropped to the caption, not to a fixed band, so the size is whatever the
    # words need; what is asserted is that it is a tight, real frame — caption
    # pixels against the flat grey, with no 9:16 dead space around them.
    assert img.width <= 720 and img.height < 720
    bg = img.getpixel((2, 2))
    assert max(img.getdata()) - bg > 60    # bright caption pixels above the grey


def test_word_preset_preview_is_animated(tmp_path):
    """A word-by-word caption cannot be judged from one frame.

    Every word-mode preset renders its first event as the same word, so four of
    the six presets were indistinguishable stills of "THIS". The preview has to
    cycle events — assert on distinct frame content, not on a frame count.
    """
    from apps.editor.subtitles import render_preview, PRESETS
    assert PRESETS["bangers"]["mode"] == "word"
    gif = render_preview("bangers", tmp_path / "b.gif")
    from PIL import Image
    img = Image.open(gif)
    assert getattr(img, "n_frames", 1) > 1
    # Distinct frames, not the same word repeated: compare bright-pixel counts.
    counts = set()
    for i in range(img.n_frames):
        img.seek(i)
        g = img.convert("L")
        counts.add(sum(1 for px in g.getdata() if px > 128))
    assert len(counts) > 1


def test_line_preset_preview_is_a_still(tmp_path):
    """A line caption shows the whole sentence in every frame, so animating it
    would only cost bytes."""
    from apps.editor.subtitles import render_preview
    out = render_preview("elegant", tmp_path / "e.png")
    from PIL import Image
    assert getattr(Image.open(out), "n_frames", 1) == 1
