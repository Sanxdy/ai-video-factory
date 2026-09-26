#!/usr/bin/env python3
"""Generate mood-specific BGM beds with ffmpeg (license-safe, self-generated).

v2 — richer, audible beds after user feedback ("cuma dengung"):
- melodic arpeggio layer on top of the pad (notes cycle, not a static drone)
- soft percussive pulse so it reads as music, not hum
- louder target: ≈ -20 dB mean (was -29.5 dB); VO still dominates via
  sidechain duck + music_db headroom in mix_audio.

Run: .venv/bin/python scripts/gen_mood_music.py
Output: content/music/<mood>.mp3
"""
import subprocess
import sys

from core.config import config

MUSIC_DIR = config.root / "content" / "music"

# Per mood: pad chord (root/third/fifth/bass), arpeggio notes over a minor or
# major scale run, tempo of the arp/pulse, and tone shaping.
MOODS = {
    # Cat Comedy / Weekend Hacks: light major, bouncy
    "playful": dict(pad=[261.63, 329.63, 392.0, 130.81],
                    arp=[523.25, 659.26, 784.0, 659.26], bpm=112,
                    lowpass=2600, gain=8),
    # Space Explained / Science Made Fun: wide airy fifth, slow shimmer
    "cosmic": dict(pad=[174.61, 261.63, 349.23, 87.31],
                   arp=[349.23, 523.25, 698.46, 523.25], bpm=72,
                   lowpass=2000, gain=9),
    # Wildlife Wonders: earthy minor with gentle movement
    "nature": dict(pad=[196.0, 233.08, 293.66, 98.0],
                   arp=[392.0, 466.16, 587.33, 466.16], bpm=84,
                   lowpass=2200, gain=8),
    # Adrenaline Friday: brighter, driving pulse
    "energetic": dict(pad=[220.0, 277.18, 329.63, 110.0],
                      arp=[440.0, 554.37, 659.26, 554.37], bpm=128,
                      lowpass=3000, gain=7),
    # Sunday Boost: warm major lift
    "uplifting": dict(pad=[233.08, 293.66, 349.23, 116.54],
                      arp=[466.16, 587.33, 698.46, 587.33], bpm=96,
                      lowpass=2400, gain=8),
    # neutral fallback
    "calm": dict(pad=[220.0, 277.18, 329.63, 110.0],
                 arp=[440.0, 523.25, 659.26, 523.25], bpm=64,
                 lowpass=2000, gain=9),
}
DUR = 90


def gen(mood: str, spec: dict) -> None:
    out = MUSIC_DIR / f"{mood}.mp3"
    beat = 60.0 / spec["bpm"]

    inputs, parts = [], []
    # 1. sustained pad chord
    for i, f in enumerate(spec["pad"]):
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency={f}:duration={DUR}:sample_rate=44100"]
        v = [0.36, 0.28, 0.24, 0.45][i]
        parts.append(f"[{i}:a]volume={v}[p{i}]")
    npad = len(spec["pad"])
    pad_mix = "".join(f"[p{i}]" for i in range(npad))
    parts.append(f"{pad_mix}amix=inputs={npad},lowpass=f={spec['lowpass']}[pad]")

    # 2. melodic arpeggio: each note is a short plucked sine repeating per bar
    arp_notes = spec["arp"]
    n_arp = len(arp_notes)
    step = round(beat * 44100)  # one note per beat
    for j, f in enumerate(arp_notes):
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency={f}:duration={DUR}:sample_rate=44100"]
        # pluck envelope via tremolo at note rate; alternate notes by phase offset
        parts.append(
            f"[{npad + j}:a]volume=0.16,"
            f"tremolo=f={1.0 / (beat * n_arp):.3f}:d=0.85,"
            f"aecho=0.7:0.5:{int(step * 0.9)}:0.35[a{j}]")

    arp_mix = "".join(f"[a{i}]" for i in range(n_arp))
    parts.append(f"{arp_mix}amix=inputs={n_arp}[arp]")

    # 3. combine + shape: pad+arp, gentle lowpass, final gain to ~-20dB mean
    parts.append(
        f"[pad][arp]amix=inputs=2:duration=first"
        f",lowpass=f={spec['lowpass']}"
        f",aecho=0.8:0.88:60|110|160:0.25|0.18|0.12"
        f",volume={spec['gain']}dB[out]")

    fc = ";".join(parts)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs + \
        ["-filter_complex", fc, "-map", "[out]", "-ar", "44100", "-ac", "2",
         "-codec:a", "libmp3lame", "-qscale:a", "4", str(out)]
    subprocess.run(cmd, check=True)

    # auto-normalize to ≈ -20 dB mean (amix attenuation is unpredictable);
    # VO dominance comes from mix_audio duck + music_db headroom, not from
    # starving the source track.
    import re

    def mean_db(p):
        r = subprocess.run(["ffmpeg", "-i", str(p), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True)
        m = re.search(r"mean_volume: ([-\d.]+) dB", r.stderr)
        return float(m.group(1)) if m else None

    cur = mean_db(out)
    if cur is not None:
        delta = round(-20.0 - cur, 1)
        tmp = out.with_suffix(".tmp.mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out),
                        "-af", f"volume={delta}dB", "-c:a", "libmp3lame",
                        "-qscale:a", "4", str(tmp)], check=True)
        tmp.replace(out)
    print(f"✓ {out.name}  {mean_db(out)} dB mean")


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(MOODS)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for m in wanted:
        gen(m, MOODS[m])
