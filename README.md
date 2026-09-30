# AI Video Factory (AVF) — Desktop App

Fully local, $0-cost AI content production pipeline: idea → research → script →
storyboard → stock footage / images → narration (Kokoro) → subtitles → FFmpeg
render (9:16, motion, subtitles, BGM) → QC → YouTube upload with playlist
filing → analytics → feedback loop.

This repository is the **AVF desktop app**: a native-window build for macOS
(with Windows/Linux targets) — double-click the app, a local server boots
behind a splash screen, and the console runs in a webview. Packaged artifacts
live in `packaging/` (`build.py` assembles the bundle, `smoke.py` verifies it).

Also runs on a modest CPU VPS (proven on Oracle ARM 4-core / 12 GB) and on
Apple Silicon. No paid APIs required — all models local (optional BYOK API
upgrade). YouTube OAuth = permanent Production token, auto-upload included.

---

## 1. Install — one command

**Prerequisites (present on almost every OS):** `git` and `make`. If not:
`sudo apt install -y git make` (Linux) / `brew install git` (Mac).

```bash
git clone https://github.com/Sanxdy/ai-video-factory.git && cd ai-video-factory && make install
```

`make install` and `./scripts/bootstrap.sh` are the same thing — use whichever
you prefer. It sets the machine up, asking nothing:

What it installs automatically:

| # | Component | Detail |
|---|---|---|
| 1 | System deps | `python3`, `ffmpeg`, `node` (+ `python3-venv` on Linux / Homebrew on Mac, auto-install) |
| 2 | Python venv | `.venv` + `pip install -e .` → all dependencies (kokoro-onnx, faster-whisper, fastapi, uvicorn, etc.) |
| 3 | Web UI | `npm install && npm run build` (frontend, ~1 minute) |
| 4 | Directories | `content/`, `runtime/`, `database/` |
| 5 | AI models | Kokoro TTS + Whisper STT (~600 MB, auto-download) |
| 6 | Health check | `avf doctor` runs automatically at the end |

- Takes ~10–15 min the first time (Kokoro + Whisper ≈600 MB).
- **Idempotent** — safe to re-run; `avf doctor` verifies everything.
- LLM = BYOK API only. No local model is pulled, and there is no local
  fallback: if the API is down the pipeline fails with a clear error.

Manual (if bootstrap does not run):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
./scripts/download_models.py     # Kokoro TTS + whisper + model weights
./scripts/system_check.py        # check the environment
```

## 2. Run

**From a source checkout** (after §1):

```bash
source .venv/bin/activate
avf serve                     # → http://localhost:8600  (host 0.0.0.0:8600)
make dev                      # dev loop: 127.0.0.1:8610, rebuilds web/out only when stale
```

**From an installer** (`.dmg` / `setup.exe` / Linux zip — build them in §7):
double-click `AVF.app` (macOS) / `AVF.exe` (Windows) / `avf.sh` (Linux). A local
server boots behind a splash screen and the console opens in a native window. No
venv, no CLI, no Terminal.

Remote access (VPS / subdomain): reverse-proxy port `8600` to your domain
(e.g. Nginx/Caddy). The UI contains: dashboard, content queue, settings, YouTube
connect, analytics.

> **Systemd (optional, VPS):** `sudo systemctl start avf-api` — the service
> already installed on the deployment server.

## 3. API Keys — Settings

All keys are entered through the Web UI → **Settings** (or the `avf` CLI below).
No key is required for $0 mode — all optional.

### 3a. BYOK API (required — the only LLM)

AVF uses an external API only ([OI]-compatible). There is no local model
and no local fallback. Settings → **AI model**:

| Field | Value |
|---|---|
| API Base URL | `https://api.openai.com/v1` (or anything [OI]-compatible) |
| API Key | your key (stored encrypted; the UI shows only `…xxxx`) |
| API Model | `gpt-4o-mini` / any model available on that endpoint |
| API Fallback Models | comma-separated, tried in order if the main model fails (429/404/5xx/timeout) |

Verify: click **Test** in Settings — it must return success. An example verified
on the VPS: base `http://localhost:20128/v1`, model
`cmc/deepseek/deepseek-v4-flash`, fallback `cmc/z-ai/glm-5.3-flash`.

### 3b. Stock footage API (required — at least one of Pexels / Pixabay)

There is no AI video generator in this repo: every `.mp4` scene asset is either
stock footage or a clip you imported yourself. Without a key, scenes fall back to
still images with a slow zoom, so `POST /api/produce` refuses with **409** until
one key is stored. Settings → **Media** (one Pexels key covers both photos and
video — "Videos API shares the image key"):

| Field | Value |
|---|---|
| Image provider | `pexels` |
| Motion mode | `stock` — real moving video |
| Stock sources | tick Pexels and/or Pixabay |
| Pexels API Key | free tier at https://www.pexels.com/api/ |
| Pixabay API Key | free tier at https://pixabay.com/api/docs/ |

`auto` as the image provider uses Pexels when a key is present and a procedural
gradient background when it is not — a coloured frame, not footage.

### 3c. YouTube (OAuth — required for upload)

Settings → **YouTube** → **Connect** button:
1. Upload `client_secret.json` (Google Cloud Console → OAuth 2.0 → Desktop app)
2. Click Connect → open the OAuth URL → pick the channel → paste the redirect URL back
3. The token is stored **permanently** (Production mode) — set up once, uploads run automatically

> ⚠️ **Required: set the OAuth consent screen → Publishing status = `In production`.**
> If left on `Testing`, the refresh token **dies after 7 days** — the uploader works
> on day 1 and dies on day 8. Google verification is not needed; just click **Publish app**.

Step-by-step guide (for installing on someone else's machine):
[`packaging/README.md`](packaging/README.md)

CLI: `avf youtube auth`

## 4. CLI Reference (every command)

```bash
source .venv/bin/activate

avf doctor                    # check environment + models + pipeline readiness
avf status                    # pipeline / queue status
avf models                    # list local models

# ── Ideas & production ──
avf idea generate --count 5   # brainstorm ideas (local LLM)
avf produce run               # full pipeline for a new project
avf produce run --project-id 12
avf produce all               # every pending project
avf produce count             # how many produced today
avf research                  # research the topic
avf script                    # write the script
avf storyboard                # storyboard
avf render                    # render the video
avf quality                   # QC check
avf preview                   # preview the video

# ── Queue & approval gate ──
avf pending                   # list videos awaiting approval
avf approve <id>              # approve → continue to upload
avf reject <id>               # reject
avf queue generate            # generate the content queue

# ── YouTube ──
avf youtube auth              # OAuth connect (permanent token)
avf upload <id>               # upload (public by default; --privacy private/unlisted)
avf analytics sync            # pull stats + rescore the idea queue

# ── Automation ──
avf daemon --target 5         # autonomous loop: analytics→produce→QC→wait for approval
avf daily                     # produce today's scheduled videos (Mon–Sun themes)
avf serve                     # web UI → http://localhost:8600
```

### Environment variables (optional)
| Var | Purpose |
|---|---|
| `AVF_AUTO_APPROVE=1` | skip the approval gate — upload immediately |
| `AVF_DAILY_VIDEOS=2` | number of videos per daily cycle |
| `AVF_LOG_LEVEL=debug` | verbose logging |

Example: auto-daily with 2 videos:
```bash
AVF_DAILY_VIDEOS=2 AVF_AUTO_APPROVE=1 avf daily
```

## 5. Daily automation (cron)

Example: daily at 19:00 WIB (2 videos, auto-approve, upload public):
```cron
0 12 * * * cd /home/ubuntu/ai-video-factory && AVF_DAILY_VIDEOS=2 AVF_AUTO_APPROVE=1 .venv/bin/python -u -c "from apps.uploader.daily import main; main()" >> /tmp/avf_daily.log 2>&1
```

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `avf doctor` fails on LLM | check the API Base URL / key / model in Settings → **AI model** |
| Settings API key error "Extra data" | Delete the old key, re-enter it — make sure there are no odd characters |
| Upload fails | Re-run `avf youtube auth`. If it repeats every ~7 days → the consent screen is still `Testing`; set it to `In production` (see [`packaging/README.md`](packaging/README.md)) |
| Double subtitles | Do not upload a video that already has subtitles baked in |

## 7. Build the installers — one command

```bash
make build            # → packaging/out/   (needs the venv from `make install`)
```

That is the whole build. It produces every installer the host can make, and **one
macOS machine builds all three platforms**: nothing is frozen on the host, because
the interpreter is a prebuilt `python-build-standalone` tarball for the *target*
and `uv` resolves that target's wheels.

| Artifact | For | User opens | One-time tool |
|---|---|---|---|
| `avf-macos-arm64.dmg` | macOS 14+, Apple Silicon | drag `AVF.app` → Applications | macOS host (`hdiutil`) + `pip install ds_store mac_alias` |
| `avf-windows-x64-setup.exe` | Windows 10 x64 | `AVF.exe`, after a one-click install | `makensis` + `mingw-w64` — the latter builds the `AVF.exe` the installer's shortcuts point at |
| `avf-linux-x64.AppImage` | Linux, glibc 2.35+ | `chmod +x avf-linux-x64.AppImage && ./avf-linux-x64.AppImage` | `mksquashfs` — `brew install squashfs` / `apt install squashfs-tools` |
| `avf-{macos-arm64,windows-x64,linux-x64}.zip` | all three | `AVF.app` / `AVF.exe` / `avf.sh` | — |

The web UI is built for you when `web/out` is missing or stale, every download is
SHA-256 pinned (a mismatch deletes the file and stops the build), and the only
thing you must supply is `uv` — `brew install uv` / `pipx install uv`.

Without `make` — on Windows, or any shell — the same build is one command too:

```bash
python3 packaging/build.py all       # or one target: macos-arm64 | windows-x64 | linux-x64
```

A missing `makensis`, `mingw-w64` or `mksquashfs` skips that one artifact and says
so; it does not fail the run, so a build always finishes with whatever the host can
produce. The DMG is the exception — `hdiutil` exists only on macOS, so building on
Linux or Windows simply leaves it out.

**Check a build before shipping it**, from the unpacked bundle root (on macOS,
`AVF.app/Contents/Resources`) — no API key, no network:

```sh
AVF_DATA_DIR=$(mktemp -d) ./python/bin/python3 smoke.py
AVF_DATA_DIR=$(mktemp -d) ./python/bin/python3 -m apps.cli doctor
```

Platform floors: macOS 14.0 (Apple Silicon), Windows 10 x64, Linux glibc 2.35
(Ubuntu 22.04+ / Debian 12+). The floors, the pinning and what `smoke.py` proves
are documented in [`packaging/README.md`](packaging/README.md).
