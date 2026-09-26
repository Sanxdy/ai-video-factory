"""Sync regression test — VO/subtitle alignment must use REAL spoken durations.

Project 18/19 bug: subtitles were timed from LLM-estimated scene durations
while the VO came from a fresh (non-deterministic) TTS pass → captions and
voice drifted/overlapped. Both caption windows and scene audio placement must
now derive from the actual wav duration.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.editor.subtitles import _wav_dur, build_ass
from apps.orchestrator.pipeline import _make_scene_srt


def _mk_wav(path: Path, seconds: float) -> Path:
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def test_wav_dur_reads_real_length(tmp_path):
    p = _mk_wav(tmp_path / "s.wav", 3.7)
    assert abs(_wav_dur(str(p)) - 3.7) < 0.1
    assert _wav_dur(str(tmp_path / "missing.wav")) is None


def test_caption_window_uses_real_voice(tmp_path):
    scenes = [{"scene_number": 1, "duration": 6.42,
               "narration": "Ever wonder how scientists find tigers"},
              {"scene_number": 2, "duration": 9.32,
               "narration": "Tigers move silently"}]
    audio = [str(_mk_wav(tmp_path / "a1.wav", 3.8)),
             str(_mk_wav(tmp_path / "a2.wav", 8.8))]
    out = build_ass(scenes, audio, tmp_path / "s.ass", preset="elegant")
    lines = [l for l in out.read_text().splitlines() if l.startswith("Dialogue:")]
    # caption 1 must END near 3.8 (real voice), NOT 6.42 (LLM estimate)
    # ASS fields: Dialogue: Layer, Start, End, Style, Name, MarginL/R/V, Effect, Text
    def _sec(ts):
        return sum(int(x) * m for x, m in zip(ts.split(":")[:2], (60, 1))) + float(ts.split(":")[2])
    s1 = _sec(lines[0].split(",", 4)[1])
    e1 = _sec(lines[0].split(",", 4)[2])
    # caption 1 window = real voice
    assert s1 < 0.01, f"caption1 start {s1}"
    assert 3.7 < e1 < 3.9, f"caption1 end {e1} (should be ~= real voice 3.8)"
    # caption 2 starts at scene-1 padded slot (max(6.42,3.8)+0.25 = 6.67),
    # NOT at the raw voice end (3.8) — matches where audio places VO 2.
    s2 = _sec(lines[1].split(",", 4)[1])
    assert 6.5 < s2 < 6.8, f"caption2 start {s2} (should be ~6.67 padded slot)"


def test_srt_window_uses_real_voice(tmp_path):
    scenes = [{"scene_number": 1, "duration": 6.42,
               "narration": "Ever wonder how scientists find tigers"},
              {"scene_number": 2, "duration": 9.32,
               "narration": "Tigers move silently through night forests"}]
    audio = [str(_mk_wav(tmp_path / "a1.wav", 3.8)),
             str(_mk_wav(tmp_path / "a2.wav", 8.8))]
    out = _make_scene_srt(scenes, audio, tmp_path / "s.srt")
    txt = out.read_text()
    # caption 1 ends ~3.8 (real), NOT 6.42 (LLM estimate)
    assert "00:00:03,799" in txt or "00:00:03,800" in txt, txt[:200]
    # caption 2 starts at padded slot ~6.67 (matches audio placement)
    assert "00:00:06,669" in txt or "00:00:06,670" in txt, txt[:300]
