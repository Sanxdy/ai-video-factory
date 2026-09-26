"""Countdown pacing: a silent hold between ranked items, and nothing else."""
from __future__ import annotations

import pytest

from apps.storyboard import storyboard_gen as sbg
from apps.editor.subtitles import build_ass
from apps.orchestrator.pipeline import _make_scene_srt, _fmt_srt_ts

# fake (non-existent) wav paths: _wav_dur returns None and the scene duration is
# used — exactly the behaviour we want to pin here
FAKE = ["a.wav", "b.wav"]


def _fake_shots(chunks):
    return [{"visual_prompt": t, "stock_query": "", "motion_prompt": "zoom-in"}
            for _h, t in chunks]


SCRIPT = {
    "hook": "Seven machines that should not work, and do.",
    "body": "## Item Seven\nIt lifts a million tonnes with a single cable.\n\n"
            "## Item Six\nIt cuts steel with nothing but water.",
    "cta": "Subscribe for the next countdown.",
}


def _two_scenes(first_pause):
    return [{"scene_number": 1, "duration": 5.0, "narration": "Number seven.",
             "pause_after": first_pause},
            {"scene_number": 2, "duration": 5.0, "narration": "Number six.",
             "pause_after": 0.0}]


def test_pause_lands_at_the_end_of_every_section_but_the_outro(monkeypatch):
    monkeypatch.setattr(sbg, "_visual_shots", _fake_shots)
    scenes = sbg._longform_scenes(SCRIPT, 5.0)
    # 4 sections (hook, item 7, item 6, cta) -> a hold at the end of the hook and
    # of each item; the outro ends the video, so it never holds
    paused = [i for i, s in enumerate(scenes) if s["pause_after"] > 0]
    assert len(paused) == 3, [(s["scene_number"], s["pause_after"]) for s in scenes]
    assert scenes[-1]["pause_after"] == 0
    assert all(scenes[i]["pause_after"] == sbg.COUNTDOWN_PAUSE for i in paused)


def test_srt_cursor_advances_over_the_pause(tmp_path):
    """The hold shifts when the NEXT caption starts; it never stretches one."""
    srt = _make_scene_srt(_two_scenes(sbg.COUNTDOWN_PAUSE), FAKE,
                          tmp_path / "s.srt").read_text()
    # first caption is untouched by the pause...
    assert f"{_fmt_srt_ts(0.0)} --> {_fmt_srt_ts(5.0)}" in srt
    # ...the second starts a full hold later (5.0 + 0.25 pad + pause)
    start = 5.25 + sbg.COUNTDOWN_PAUSE
    assert f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(start + 5.0)}" in srt


def test_ass_cursor_advances_over_the_pause(tmp_path):
    def second_start(pause, path):
        build_ass(_two_scenes(pause), FAKE, path, preset="karaoke")
        dlg = [l for l in path.read_text().splitlines() if l.startswith("Dialogue:")]
        return _ass_secs(dlg[1].split(",")[1])
    a = second_start(0.0, tmp_path / "no.ass")
    b = second_start(sbg.COUNTDOWN_PAUSE, tmp_path / "yes.ass")
    assert b - a == pytest.approx(sbg.COUNTDOWN_PAUSE)


def test_a_short_has_no_pause(tmp_path):
    scenes = [dict(s, pause_after=0.0) for s in _two_scenes(0.0)]
    srt = _make_scene_srt(scenes, FAKE, tmp_path / "x.srt").read_text()
    assert f"{_fmt_srt_ts(5.25)} --> {_fmt_srt_ts(10.25)}" in srt


def _ass_secs(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)
