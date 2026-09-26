# Distribution & First-Run Setup

For anyone installing AVF on **their own** machine with **their own** YouTube
channel. The install steps themselves are in the main [`README.md`](../README.md)
(`./scripts/bootstrap.sh` does everything). This document covers what that README
does *not*: the accounts and keys each new user must bring, and the one Google
setting that silently breaks the uploader after a week.

---

## What each user must supply

AVF is **BYOK — bring your own keys**. Nothing is shared with the person who
wrote the software; nothing phones home. Four things, all free:

| # | Item | Where to get it | Required |
|---|---|---|---|
| 1 | LLM API (OpenAI-compatible) | any provider: base URL + key + model name | **yes** — the only LLM AVF uses |
| 2 | YouTube OAuth client | Google Cloud Console (steps below) | **yes** for uploading |
| 3 | Pexels API key | https://www.pexels.com/api/ | recommended (stock footage) |
| 4 | Pixabay API key | https://pixabay.com/api/docs/ | optional (second stock source) |

Their channel, their quota, their uploads. Your own channel and credentials are
never involved.

---

## ⚠️ The Google OAuth trap — read this before connecting

Every user authorizes their **own** channel with their **own** OAuth client, so
no central Google verification is required. But there is one setting that decides
whether the uploader works long-term or dies in a week:

> **An OAuth consent screen left in `Testing` publishing status expires every
> refresh token after 7 days.**
>
> Symptom: everything works on day 1. On day 8, uploads start failing with an
> auth error and the user has to reconnect. It looks like a bug in AVF. It is not.

**Fix — free, no verification needed:** set the consent screen's publishing status
to **`In production`**. Google allows an unverified app to be published to
production; users just see a "Google hasn't verified this app" screen and click
**Advanced → Continue**. This removes *both* the 7-day token expiry *and* the
100-test-user allowlist cap.

---

## Step-by-step: Google OAuth for uploads

Do this once per machine. Takes about 5 minutes.

**1. Create a Google Cloud project**
→ https://console.cloud.google.com/projectcreate

**2. Enable the YouTube Data API v3**
→ https://console.cloud.google.com/apis/library/youtube.googleapis.com
→ click **Enable**

**3. Configure the OAuth consent screen**
→ https://console.cloud.google.com/apis/credentials/consent

- User type: **External**
- App name / support email: anything (only you see it)
- **Scopes:** add `https://www.googleapis.com/auth/youtube.upload`
  (optionally also `.../auth/youtube.readonly` for analytics)

**4. ⚠️ Set publishing status to `In production`** ← *the step everyone misses*

- On the consent screen page, click **Publish app** → confirm.
- Do **not** leave it on `Testing`. See the trap above.
- You do **not** need to complete Google's verification review for this.

**5. Create the OAuth client**

- → https://console.cloud.google.com/apis/credentials
- **Create credentials** → **OAuth client ID**
- Application type: **Desktop app**
- Download the JSON → rename to **`client_secret.json`**

**6. Connect inside AVF**

- Start the server: `avf serve` → http://localhost:8600
- Settings → **YouTube** → upload `client_secret.json` → click **Connect**
- A browser opens → pick the channel → approve
- (CLI equivalent: `avf youtube auth`)

Token is stored locally and refreshes automatically — with step 4 done, it does
not expire.

---

## First-run checklist

```
[ ] ./scripts/bootstrap.sh            # python + ffmpeg + web UI + models
[ ] avf serve                         # → http://localhost:8600
[ ] Settings → AI model               # base URL + key + model, click Test
[ ] Settings → Media                  # Pexels key (+ Pixabay optional)
[ ] Google OAuth steps 1–6 above      # status In production, then Connect
[ ] avf doctor                        # everything green?
[ ] avf produce run                   # first video end-to-end
```

**Rendering speed:** CPU-only, no GPU needed. Expect roughly 6× realtime on a
2-core box (a 6-minute video ≈ 34 min). A modern laptop is considerably faster
than that reference.

**Already been running AVF from a source checkout?** The app keeps its data in the
platform folder (`~/Library/Application Support/AVF` on macOS, `%APPDATA%\AVF` on
Windows, `$XDG_DATA_HOME/avf` on Linux), not next to the code, because a bundle may
sit somewhere read-only or get replaced on update. On the first launch it copies
`content/`, `runtime/` and `database/` across from a checkout it can find — one of
the three folders above the bundle that has both `core/config.py` and
`database/factory.db`. The original is left untouched. To point it at a checkout
somewhere else, or to carry the data over after that first launch:

```sh
AVF_MIGRATE_FROM=/path/to/vortex-video-generator ./AVF.app/Contents/MacOS/AVF
```

It never overwrites a data folder that already has a `database/factory.db`, so
re-running it is harmless.

---

## Notes for whoever builds the installer

```sh
cd web && npm run build          # packaging does not build the UI for you
python3 packaging/build.py all   # → packaging/out/avf-{macos-arm64,windows-x64,linux-x64}.zip
```

One script, three targets, **from any one of them** — a macOS machine produces the
Windows and Linux zips too. Nothing is frozen on the host: the interpreter is a
prebuilt `python-build-standalone` tarball for the *target*, and `uv pip install
--python-platform <target> --only-binary :all:` resolves every wheel for it.
PyInstaller cannot do this, because it bundles the host interpreter and its
`.pyd`/`.dylib` files. Every download is SHA-256 pinned; a mismatch deletes the
file and stops the build.

**Check a build before shipping it** — from the unpacked bundle root (on macOS,
from `AVF.app/Contents/Resources`), no API key and no network needed:

```sh
AVF_DATA_DIR=$(mktemp -d) ./python/bin/python3 smoke.py    # render stack
AVF_DATA_DIR=$(mktemp -d) ./python/bin/python3 -m apps.cli doctor
```

`smoke.py` renders a synthetic Short through the app's own editor functions and
asserts that `drawtext` and libass **painted pixels** — not merely that ffmpeg
exited 0, since a missing font makes both fail silently. It also fails if the
render used a host ffmpeg instead of the bundled one.

### Entry points

Unzipping gives one folder per OS. What the user double-clicks differs everywhere;
`desktop.py` behind it does not.

| OS | Double-click | Notes |
|---|---|---|
| macOS | `AVF.app` | Finder runs `Contents/MacOS/AVF` → `Resources/python/bin/python3`. A real bundle, so it gets a dock icon and a menu bar and opens no Terminal. Unsigned — first launch needs right-click → **Open**. |
| Windows | `AVF.bat` | `start "" python\pythonw.exe desktop.py`. `pythonw`, not `python`, so no console window appears; a crash is only visible in `runtime/logs/desktop.log`. |
| Linux | `avf.sh` | `avf.desktop` runs it from wherever the folder was unzipped (`%k`), so nothing is baked in at build time. Copy it into `~/.local/share/applications/` for a menu entry. |

Closing the window mid-render asks first, and the job resumes on the next start.
If `pywebview` cannot import — Linux without `webkit2gtk` — the shell opens the
system browser instead and prints the URL: same app, no window frame.

### Platform floors

| OS | Minimum | Set by |
|---|---|---|
| macOS | 14.0 (Sonoma), Apple Silicon | `MACOSX_DEPLOYMENT_TARGET`, keeps dependency versions identical across the three targets |
| Windows | 10 x64 | wheels |
| Linux | glibc **2.35** — Ubuntu 22.04+, Debian 12+ | the ffmpeg build, not the wheels (they stop at 2.29) |

The Linux floor is checked at build time (`verify()` measures the bundled
binaries), so swapping in an ffmpeg built on a newer distro fails the build
instead of failing on a user's older machine. Ubuntu 20.04 and older are **not**
supported.

### Two things that will bite you

- **ffmpeg version changes the encoder.** The reference VPS runs 4.2, which lacks
  `amix:normalize=0` and forced a workaround in `apps/editor/media_pipeline.py`.
  The bundle ships 9.0.2. Diff duration, loudness and QC against 4.2 output
  before trusting it — the bundled build was validated with `packaging/smoke.py`,
  which is not the same as comparing real renders.
- **Bundled ffmpeg must keep `subtitles` and `drawtext`.** They are the whole
  reason `core/binaries.py` probes `-filters` instead of trusting PATH; brew's
  plain build has neither. A replacement binary that lacks them passes every
  file-level check and silently drops every caption.

**Bundle size:** ~2 MB app code + 1.1 MB web UI + ~200 MB Python (no torch) +
~130 MB ffmpeg + ~340 MB models downloaded on first run. `torch`, `cv2`,
`diffusers` and `torchaudio` were dropped from the project — nothing in AVF
imports them.

## DMG styling (macOS)

The `.dmg` is styled like a product installer: branded background, fixed icon
positions, hidden window chrome, custom volume icon. `build.py` writes these as
a `.DS_Store` on the mounted volume — the same records electron-builder
produces — so it needs two build-time packages in the venv that runs it:

    pip install ds_store mac_alias

The background art lives at `packaging/dmg-background.png` (1320×840, 2x of the
660×420 window). Icon slots drawn into it must match the `Iloc` positions in
`build.py::_style_dmg_volume`: `AVF.app` at (160, 240), `Applications` at
(470, 240).
