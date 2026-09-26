"""Background music (spec §19-20) — optional, license-safe.

Sources, in priority order:
  1. <data>/content/music/<channel>.mp3  (user-provided; license recorded in
     that dir's LICENSES.md — never auto-download copyrighted files)
  2. assets/music/<channel>.mp3          (the CC0 beds that ship with the app)
  3. none → narration-only (always allowed)

The two dirs are searched, not copied between: the shipped beds stay read-only
beside the code and the user's folder only ever holds what the user put there.
An earlier version copied the beds into DATA_DIR on first run, which cost 39 MB
per fresh install (and per test) for no benefit.

Mixing (§20): narration dominant, music ducked via sidechain, loudnorm last.
"""
from __future__ import annotations

from pathlib import Path

from core.binaries import ffmpeg_path
from core.config import ROOT, data_dir
from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("music")

# CC0 beds that ship with the app (assets/music/LICENSES.md).
BUNDLED = ROOT / "assets" / "music"

EXTS = (".mp3", ".wav")


def music_dir() -> Path:
    """Where the user's own beds live. May not exist."""
    return data_dir() / "content" / "music"


def music_dirs() -> tuple[Path, ...]:
    """Search order: the user's folder first, then the shipped beds."""
    return (music_dir(), BUNDLED)


def resolve_bed(name: str) -> Path | None:
    """A bed by bare filename. The user's copy shadows the shipped one."""
    if not name:
        return None
    for d in music_dirs():
        p = d / name
        if p.is_file() and p.suffix.lower() in EXTS:
            return p
    return None


def list_beds() -> list[str]:
    """Every selectable bed, deduped by filename, user copies first."""
    names = {p.name for d in music_dirs() if d.is_dir()
             for p in d.iterdir() if p.suffix.lower() in EXTS}
    return sorted(names)


def find_music(channel: str | None = None) -> Path | None:
    """Return first available music bed, or None."""
    for name in ([f"{channel}.mp3", f"{channel}.wav"] if channel else []) + \
            ["default.mp3", "default.wav"]:
        p = resolve_bed(name)
        if p:
            log.info("music bed: %s", p.name)
            return p
    log.info("no music bed found — narration only")
    return None


def mix_audio(narration: Path, music: Path | None, out: Path,
              music_db: float | None = None,
              voice_volume: int | None = None,
              music_volume: int | None = None) -> Path:
    """Narration + ducked music → loudnorm → out.wav (spec §20).

    Args:
        music_db: legacy dB attenuation (deprecated, kept for compat).
        voice_volume: 0-100 user level for narration loudness.
        music_volume: 0-100 user level for music bed loudness.
    """
    from apps.editor.audio_levels import (vol_to_db, bed_gain_db,
                                          default_voice_volume,
                                          default_music_volume)
    if voice_volume is None:
        voice_volume = default_voice_volume()
    if music_volume is None:
        music_volume = default_music_volume()

    # Narration volume in dB (0-100 -> -60..0)
    voice_db = vol_to_db(voice_volume)

    # Legacy music_db override still wins if explicitly passed (render-time override).
    if music_db is not None:
        music_db_val = float(music_db)
    else:
        music_db_val = bed_gain_db(voice_db, music_volume)

    out = Path(out)
    if music is None:
        # narration only: apply the voice level, then one fixed gain
        import subprocess
        from apps.editor.editor import normalize_linear
        tmp = out.with_name(out.stem + ".pre.wav")
        r = subprocess.run([ffmpeg_path(), "-y", "-i", str(narration),
                            "-af", f"volume={voice_db}dB", "-ar", "44100",
                            str(tmp)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ProviderError(f"ffmpeg loudnorm failed: {r.stderr[-300:]}")
        normalize_linear(tmp, out)
        tmp.unlink(missing_ok=True)
        return out

    import subprocess
    from apps.editor.editor import normalize_linear
    # sidechaincompress: music ducks whenever narration plays. No loudnorm in
    # this chain: a dynamic normalizer would ride the bed up in the gaps and
    # undo the ducking (see linear_loudnorm_filter).
    # The bed MUST be looped: the shipped beds are 50-157 s while a long-form
    # video is 3-6 min, so an unlooped bed simply stops mid-video (the user
    # hears the music cut out). amix's duration=first still ends the mix at the
    # narration length, so the loop is bounded by the voice, not by the bed.
    filt = (
        f"[1:a]aloop=loop=-1:size=2e9,volume={music_db_val}dB[bg];"
        f"[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=50:"
        f"release=500[duck];"
        f"[0:a]volume={voice_db}dB[vo];"
        f"[vo][duck]amix=inputs=2:duration=first:dropout_transition=3[out]"
    )
    tmp = out.with_name(out.stem + ".pre.wav")
    r = subprocess.run(
        [ffmpeg_path(), "-y", "-i", str(narration), "-i", str(music),
         "-filter_complex", filt, "-map", "[out]", "-ar", "44100", str(tmp)],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ProviderError(f"ffmpeg music mix failed: {r.stderr[-300:]}")
    normalize_linear(tmp, out)
    tmp.unlink(missing_ok=True)
    return out
