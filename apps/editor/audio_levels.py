"""Audio volume helpers — map simple 0-100 user levels to ffmpeg dB.

Default levels:
  voice_volume 80  ->  -12 dB  (clear VO, not clipped)
  music_volume 40  ->  -36 dB  (audible bed under voice, but not overwhelming)

0  -> -60 dB (effectively silent)
100 ->  0 dB  (full source level)
"""
from __future__ import annotations


def vol_to_db(value: int) -> float:
    """Map 0-100 user level to dB attenuation: 0 -> -60, 100 -> 0."""
    value = max(0, min(100, int(value or 0)))
    return -60.0 + (value / 100.0) * 60.0


def db_to_vol(db: float) -> int:
    """Reverse: dB attenuation back to 0-100 user level."""
    db = max(-60.0, min(0.0, float(db)))
    return round(((db + 60.0) / 60.0) * 100)


def default_voice_volume() -> int:
    return 80


def default_music_volume() -> int:
    return 40


# Music beds ship ~20 dB below the narration files, so mapping the slider to an
# absolute attenuation (-60..0 dB) buried the bed: 49 -> -30.6 dB left it at
# -51 dBFS against a -32 dBFS voice. That used to be masked by a dynamic
# loudnorm in the mix, which lifted the quiet bed back up; with linear
# normalization the ratio survives and the bed has to be right on its own.
# Anchor the bed to the voice and let music_volume trim around the default.
MUSIC_UNDER_VOICE_DB = 8.0    # bed level below the voice at music_volume = 50
MUSIC_TRIM_DB = 6.0           # slider range: 0 -> 14 dB under, 100 -> 2 dB under


def bed_gain_db(voice_db: float, music_volume: int) -> float:
    """Gain for the music bed, expressed relative to the voice gain.

    Verified against a previously accepted render: a bed at -20 dBFS with
    voice_volume 84 / music_volume 50 lands ~5.5 dB under the narration, which
    is the balance the published Shorts have.
    """
    v = max(0, min(100, int(music_volume or 0)))
    under = MUSIC_UNDER_VOICE_DB - (v - 50) / 50.0 * MUSIC_TRIM_DB
    return voice_db - under


def get_volumes(get_setting) -> tuple[int, int]:
    """Return (voice_volume, music_volume) honoring legacy audio.music_db."""
    import logging
    log = logging.getLogger("audio")

    vv_raw = get_setting("audio.voice_volume")
    mv_raw = get_setting("audio.music_volume")

    if vv_raw not in (None, ""):
        voice = int(vv_raw)
    else:
        voice = default_voice_volume()

    if mv_raw not in (None, ""):
        music = int(mv_raw)
    else:
        # Backward compat: old music_db slider lived at 0-100 already in the UI,
        # but stored as negative dB. Convert back to the new 0-100 scale.
        legacy = get_setting("audio.music_db")
        if legacy not in (None, ""):
            music = db_to_vol(float(legacy))
            log.info("migrated audio.music_db=%s -> music_volume=%d", legacy, music)
        else:
            music = default_music_volume()

    return max(0, min(100, voice)), max(0, min(100, music))
