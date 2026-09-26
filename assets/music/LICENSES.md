# Music Licenses — assets/music/

All beds are CC0 (public domain) from OpenGameArt.org — safe for monetized
YouTube use, attribution not required (recorded here anyway).

| File | Track | Author | Source | License |
|------|-------|--------|--------|---------|
| calm.mp3 | Crystal Cave | Pro-Sensory | opengameart.org/content/crystal-cave | CC0 |
| playful.mp3 | Retro Game Music Pack — Level 1 | Juhani Junkala (subspaceaudio) | opengameart.org/content/5-chiptunes-action | CC0 |
| cosmic.mp3 | Boss Space Battle | Pro-Sensory | opengameart.org/content/boss-battle-3 | CC0 |
| uplifting.mp3 | A New Town (RPG theme) | cynicmusic | opengameart.org/content/a-new-town-rpg-theme | CC0 |
| energetic.mp3 | Battle Theme A | cynicmusic | opengameart.org/content/battle-theme-a | CC0 |
| nature.mp3 | Peaceful Tune 2 | tozan | opengameart.org/content/peaceful-tune-2 | CC0 |
| deep-pulse.mp3 | Pulse | SpiderDave | opengameart.org/content/pulse | CC0 |
| calm-drone.mp3 | Calm Loop (looped ×6) | wipics | opengameart.org/content/calm-loop | CC0 |
| suspense.mp3 | Upside Down Grin (freaky Ambient) | haeldb | opengameart.org/content/upside-down-grin-freaky-ambient | CC0 |
| rain.mp3 | Rain (Ambient), long version | Ove Melaa | opengameart.org/content/rain-ambient-not-loopable-2-versions-available | CC0 |
| scifi.mp3 | Scifi City — Ambient Loop (looped ×6) | tinyworlds | opengameart.org/content/scifi-city-ambient-loop | CC0 |
| horror.mp3 | Ambient Horror (looped ×6) | techiew | opengameart.org/content/ambient-horror | CC0 |
| mystery.mp3 | Reversing Time / Stuck in Time (looped ×6) | isaiah658 | opengameart.org/content/reversing-time-stuck-in-time | CC0 |
| epic.mp3 | Horde War Drums Loop (looped ×10) | William Hector | opengameart.org/content/horde-war-drums-loop | CC0 |
| lofi.mp3 | Cerhern | cinameng | opengameart.org/content/cerhern | CC0 |

All tracks normalized to −20 dB mean volume. Voice dominance in mixes comes
from sidechain ducking + music_db headroom (apps/editor/music.py).

`lofi.mp3` additionally has its first 40 s trimmed. Cerhern opens with ~6 s of
digital silence (−78 dBFS) and then fades in over a minute, so normalizing by the
whole-file mean left the head 58 dB below the track's own body level — inaudible
in the preview and at the start of every render that used it, since `aloop`
starts the bed at t=0. Trimmed losslessly (`-c copy`) to the first point where the
bed reaches −27 dBFS, the level of the quietest bed's head (rain); the trimmed
head sits at −26.7 dBFS against a −18.8 dBFS body and an unchanged −1.0 dBFS peak,
and 294 s remain. Only the inaudible lead-in was removed.

Beds under ~30 s are looped end-to-end with `ffmpeg -stream_loop N` before
normalizing, so a bed always outlasts a scene without leaning on the mixer's
`aloop` to hide the seam:

    ffmpeg -stream_loop 5 -i src.ogg -c:a libmp3lame -q:a 5 out.mp3

Then normalize: measure `mean_volume` with `ffmpeg -af volumedetect`, apply
`volume=(-20 − mean)dB`, re-encode. Verify a file is real music rather than a
drone by its crest factor (max − mean): music is 8–17 dB, a sine pad ≈ 3 dB.

## How these ship

These files are tracked in the repo and read straight from here — nothing is
copied on first run. `apps/editor/music.py` searches two folders and takes the
first hit: `<data>/content/music/` (the user's own beds, shadowing by filename)
then this folder. Settings → Audio and the mix preview list the union, so a
fresh install already has beds and the user's folder only ever holds what the
user put there.

To add a bed: drop the file here, add a row above, and it ships in the next
bundle. `packaging/build.py` copies `assets/` into the bundle and its
`REQUIRED_SITE` check fails the build if `assets/music/LICENSES.md` is missing,
because an empty bed set is otherwise invisible until someone renders.
