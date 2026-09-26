"""Binary resolver: bundled copy wins, capability probe gates the choice."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import binaries


def test_resolves_runnable_ffmpeg_and_ffprobe():
    for p in (binaries.ffmpeg_path(), binaries.ffprobe_path()):
        assert Path(p).is_file() or p in ("ffmpeg", "ffprobe"), p


def test_bundled_build_wins_over_path(tmp_path, monkeypatch):
    """A vendor/ copy that advertises the filter must beat PATH."""
    fake = tmp_path / "vendor" / "ffmpeg"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\necho ' T.. subtitles V->V Render subtitles'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(binaries, "ROOT", tmp_path)
    monkeypatch.delenv("AVF_FFMPEG", raising=False)
    monkeypatch.setattr(binaries, "_FILTERS", {})
    assert binaries.ffmpeg_path("subtitles") == str(fake)


def test_build_without_filter_is_skipped(tmp_path, monkeypatch):
    """Bundled copy lacking the filter falls through to the PATH hit."""
    fake = tmp_path / "vendor" / "ffmpeg"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\necho ' ... drawbox V->V Draw a box'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(binaries, "ROOT", tmp_path)
    monkeypatch.delenv("AVF_FFMPEG", raising=False)
    monkeypatch.setattr(binaries, "_FILTERS", {})
    assert binaries.ffmpeg_path("subtitles") != str(fake)


def test_font_path_returns_an_existing_file():
    p = binaries.font_path()
    assert p and Path(p).is_file(), p
