# AVF Distribution Plan — letting other people run their own instance

> **For Hermes:** Execute task-by-task with `subagent-driven-development`, or work the phases in order.

**Goal:** Ship AI Video Factory so a third party can install it on their own machine, connect their own YouTube channel, and produce/upload videos without touching the VPS.

**Verdict:** Technically possible and not especially hard. The hard part is **not** packaging — it is Google's OAuth policy for the `youtube.upload` scope, which gates every third-party distribution and cannot be engineered around.

**Architecture (current):** Next.js static export (1.1 MB) → FastAPI on `:8600` → job system → workers. Single SQLite DB, single YouTube token, CPU-only. One install = one channel.

**Tech stack for distribution:** PyInstaller (`--onedir`) for the Python sidecar + Tauri shell for the window, or Docker for the zero-rewrite path.

---

## 1. Measured footprint (what actually has to ship)

| Component | Size | Notes |
|---|---|---|
| App code (`apps/ core/ providers/ prompts/ config/`) | **~2 MB** | `ROOT = Path(__file__).resolve().parents[1]` — already portable |
| Web UI (`web/out`) | **1.1 MB** | already `output: "export"`; served by FastAPI, no Node needed at runtime |
| Python 3.11 + ~95 packages | **~200 MB** | torch / cv2 / diffusers dropped |
| ffmpeg + ffprobe | **~80 MB** | must be bundled; the OS copy cannot be assumed |
| Kokoro + Whisper models | **~340 MB** | downloaded on first run — `_ensure_models()` already does this |
| **Installer (compressed)** | **~400 MB** | ~800 MB installed |

**520 MB already removed:** `torch` (435 MB), `cv2` (46 MB), `diffusers` (36 MB) and `torchaudio` were dropped from `pyproject.toml`; nothing in AVF imports them.

Verify before relying on it:

```bash
grep -rln "^import torch\|import cv2\|import diffusers\|import torchaudio" apps core providers --include=*.py
# must print nothing
```

**What stays:** `onnxruntime` + `kokoro-onnx` (53 MB, TTS), `ctranslate2` + `faster-whisper` (59 MB, `providers/stt/whisper.py` subtitle sync), `numpy`, `scipy`.

**Rendering is FASTER for them than for us.** Our 2-core VPS does a 6-minute 16:9 render in ~34 min (6.1x realtime). An 8-core laptop does it in a fraction of that. The desktop app is a better host than the VPS.

---

## 2. The three blockers, ranked

### Blocker 1 — Google OAuth verification (policy, not code; weeks)

Every user needs their own Google Cloud project + OAuth client with the `youtube.upload` scope. While the app is **unverified**, Google shows a "this app isn't verified" interstitial and caps it at **100 test users**. Beyond that, the app must pass Google's OAuth verification (privacy policy URL, demo video, scope justification, security review) — typically weeks, and `youtube.upload` is a **sensitive/restricted** scope.

**The owner's read is correct: per-user OAuth accounts make this safe.** Each user authorizes their *own* channel with their *own* client and their *own* quota — nothing touches the owner's channel or credentials. But there is a trap that bites almost everyone here:

> ⚠️ **An OAuth consent screen left in `Testing` status expires every refresh token after 7 days.**
> (Verified: Google's documented behaviour, and the single most common "my uploader broke after a week" report.)
> Users would have to re-authorize *weekly*. The uploader would look fine on day 1 and dead on day 8.

**The fix costs nothing and needs no verification:** flip the consent screen's publishing status from `Testing` to **`In production`**. Publishing an unverified app to production is allowed — users simply see the "Google hasn't verified this app" screen and click **Advanced → Continue**. This removes *both* the 7-day token death *and* the 100-test-user allowlist cap.

So the setup each user (or the owner, shipping one shared client) must follow:

1. Create a Google Cloud project → enable **YouTube Data API v3**
2. OAuth consent screen → **External** → add scope `youtube.upload`
3. **Publishing status → `In production`** ← the step everyone misses
4. Create an OAuth client of type **Desktop app** → download `client_secret.json`
5. Drop it into Settings → YouTube, connect once

Options:
- **Ship unverified, document the 5 steps above.** Fine for any user count, since each user brings their own client. This is the pragmatic path and what the plan assumes.
- **Verify the app centrally.** Only worth it if the owner wants to ship *one* shared OAuth client so users skip steps 1–4 — then the interstitial is gone and no one has to touch Google Cloud. Requires a privacy policy + support address + weeks of review. **Defer until demand proves it.**

**Nothing in Phase 1/2 is blocked by this** — but write the 5 steps into the first-run docs, because step 3 is the one that silently breaks people a week later.

### Blocker 2 — BYOK keys (each user brings their own)

| Key | Required | Cost | Where |
|---|---|---|---|
| LLM (OpenAI-compatible: `llm.api_base_url` + `llm.api_key`) | yes | their own | Settings → AI |
| Pexels | yes | free | Settings → Media |
| Pixabay | optional | free | Settings → Media |
| YouTube OAuth (`client_secret.json` + token) | yes | free | Settings → YouTube |

Nothing to build — the Settings page already covers all of these. What is missing is a **first-run gate** that walks a new user through them (§4, Task 6).

### Blocker 3 — Cross-platform builds

Python native wheels and ffmpeg binaries are per-platform; a `.dmg` cannot be produced on Linux. We have **macOS Apple Silicon + Ubuntu**. Windows needs a VM or CI. Use GitHub Actions matrix (`macos-14`, `ubuntu-22.04`, `windows-2022`) — free for public repos, and the repo is private so it needs a runner budget check.

---

## 3. Approach options

| Option | Effort | Artifact | Verdict |
|---|---|---|---|
| **A. Docker image** | **1–2 days** | `docker run` → `http://localhost:8600` | **Do this first.** One artifact, all 3 OSes, no Python bundling, no APP_DIR refactor needed. Proves the BYOK flow end-to-end with a real outside user. |
| **B. Tauri desktop app** | **2–3 weeks** | `.dmg` / `.exe` / `.AppImage` | **What the owner asked for.** Small shell (~10 MB) over the existing 1.1 MB static export; Python sidecar via PyInstaller. |
| C. Electron | 3–4 weeks | same | No advantage here — the UI is a static export, so Electron's Node runtime buys nothing and costs ~150 MB. |
| D. Hosted multi-tenant SaaS | 2–3 months | nothing to install | Needs per-user DB isolation, quota accounting, abuse control. Current app is single-DB/single-token — this is a rewrite, not a package. **Defer.** |

**Recommendation: A → B.** Docker is the cheapest way to discover what breaks for a stranger (missing keys, missing ffmpeg, first-run confusion) while the surface is small. The Tauri app then wraps a bundle that is already known to work.

---

## 4. Phase 1 — Docker (1–2 days)

**Objective:** a stranger can `docker run` and reach the UI.

### Task 1.1 — Multi-stage Dockerfile

Create `packaging/Dockerfile`:

```dockerfile
# ---- build the web UI (Node never ships) ----
FROM node:20-slim AS web
WORKDIR /app/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build          # writes web/out (1.1 MB static export)

# ---- runtime ----
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY apps/ core/ providers/ prompts/ config/ scripts/ ./
RUN pip install --no-cache-dir -e .        # no torch/cv2 in the dep tree
COPY --from=web /app/web/out ./web/out
ENV AVF_DATA_DIR=/data
VOLUME /data
EXPOSE 8600
CMD ["avf", "serve", "--host", "0.0.0.0"]
```

**Verify:** `docker build -f packaging/Dockerfile -t avf:dev . && docker run -p 8600:8600 -v avfdata:/data avf:dev` → `curl -sI localhost:8600` returns 200.

### Task 1.2 — Split APP_DIR from DATA_DIR (the enabling refactor)

**This is the one real code change, and Phase 2 needs it too.** Today `content/`, `runtime/`, `database/` all live under `ROOT`, which in a packaged app is read-only (macOS `.app` bundles are signed and immutable).

- Modify `core/config.py`: add `DATA_DIR`, resolved from `AVF_DATA_DIR` env → else platform default (`~/Library/Application Support/AVF`, `%APPDATA%\AVF`, `~/.local/share/avf`).
- Modify `core/filesystem.py`: `content/`, `runtime/`, `database/` resolve under `DATA_DIR`; `prompts/`, `config/`, `web/out/` stay under `APP_DIR`.
- Modify `apps/uploader/schedule.py:222` — `LOG_FILE = "/home/ubuntu/avf_daily.log"` → `DATA_DIR / "avf_daily.log"`.

**Verify:** run the suite with `AVF_DATA_DIR=$(mktemp -d)` and assert a fresh DB + content tree is created there and nothing is written under `ROOT`. Existing 170 tests must stay green — they use an isolated DB via `conftest.py`, so this is a low-risk change.

### Task 1.3 — First-run documentation

Create `packaging/README.docker.md`: the four keys a user must supply, how to make a Google OAuth client, and the exact `docker run` line. Link it from the main README.

**Verify:** hand it to one person who has never seen AVF and watch where they get stuck. That session is the real acceptance test for Phase 1.

---

## 5. Phase 2 — Tauri desktop app (2–3 weeks)

**Objective:** `.dmg`, `.exe`, `.AppImage` that open a window with the UI, no terminal.

### Task 2.1 — Freeze the Python backend with PyInstaller

Create `packaging/avf.spec` (use `--onedir`, not `--onefile`: startup is far faster and data files are trivial to add).

Collect as data: `prompts/`, `config/`, `web/out/`, plus `kokoro_onnx` and `ctranslate2` native libs. Exclude `torch`, `cv2`, `diffusers`, `torchaudio`.

**Entry point:** a thin `packaging/sidecar.py` that calls `uvicorn.run("apps.api.server:app", host="127.0.0.1", port=8600)` — the shell must not import the web app directly.

**Verify:** `dist/avf-sidecar/avf-sidecar` starts on the build machine, serves `localhost:8600`, and produces one Short end-to-end with a real LLM key. **This is the gate for the whole phase** — if the freeze is broken, nothing downstream matters.

### Task 2.2 — Bundle a static ffmpeg

Create `scripts/fetch_ffmpeg.py`: download the static build per platform (`evermeet` macOS, `johnvansickle` Linux, `gyan.dev` Windows) into `packaging/vendor/ffmpeg/<platform>/`.

Modify `apps/editor/editor.py` (and `ffprobe` callers) to resolve ffmpeg from a bundled path first, `PATH` second.

**Risk — flag for review:** the VPS runs **ffmpeg 4.2**, which lacks `amix:normalize=0` and forced the `amix` workaround in `apps/editor/media_pipeline.py`. A bundled 6.x **fixes** that, but changes encoder output. Do not assume it is a drop-in: run one Short and one 16:9 long-form through the bundled binary and diff duration, loudness ramp, and QC result against the 4.2 output before shipping.

### Task 2.3 — Tauri shell

Create `desktop/src-tauri/tauri.conf.json` + `desktop/src-tauri/src/main.rs`:

- spawn the sidecar on startup, wait for `:8600` to answer (poll `/api/health`, do not blind-sleep)
- open the window on `http://127.0.0.1:8600`
- kill the sidecar on window close (otherwise a zombie holds the port on relaunch)
- pick a free port if 8600 is taken, and pass it to both sidecar and webview

**Verify:** launch the built app, confirm the window loads the dashboard, then quit and confirm the port is released (`lsof -i :8600` empty).

### Task 2.4 — First-run wizard

On first launch (`settings` table empty), gate the UI behind a short wizard: LLM base URL + key + model → Pexels/Pixabay key → YouTube OAuth connect → output folder.

This is mostly **routing, not new UI** — the Settings page already has every field. Add a gate component in `web/app/page.tsx` that redirects to `/settings?firstrun=1` when required settings are absent.

**Verify (per the owner's UI rule):** send a mockup for approval **before** implementing. Then Playwright against `localhost:8600` with a fresh `AVF_DATA_DIR` — assert the wizard appears, and that it disappears once the keys are saved.

### Task 2.5 — Cross-platform build + CI

Create `.github/workflows/release.yml` with a `macos-14` / `ubuntu-22.04` / `windows-2022` matrix running PyInstaller then Tauri, uploading artifacts to a GitHub Release. Tag `v*` to trigger.

**Verify:** download the artifact from the Release on a machine that has never built AVF and install it. Per the standing rule, **never host installers on the VPS** — download links point at GitHub Releases.

---

## 6. Phase 3 — deferred

Hosted multi-tenant version. Only if organic demand appears. Requires per-user DB isolation, per-user quota accounting, and abuse control — a rewrite of the storage and auth layers, not a packaging exercise. **Do not start this.**

---

## 7. Risks and open questions

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Google OAuth verification** is the real gate, and it is policy — no engineering shortcut | Ship unverified with per-user OAuth clients (≤100 users); decide on verification when demand appears |
| 2 | Bundled ffmpeg 6.x changes encoder output vs the VPS's 4.2 | Diff duration/loudness/QC on one Short + one long-form before release (Task 2.2) |
| 3 | macOS: unsigned apps are blocked by Gatekeeper | Needs an Apple Developer account ($99/yr) for notarization. **Cost decision for the owner.** |
| 4 | Windows builds cannot be made on the available machines | GitHub Actions runner, or a Windows VM |
| 5 | YouTube API TOS: the tool automates uploads | Each user authorizes their own channel with their own quota — the standard BYOK pattern. Worth reading the API Services Terms before public release. |
| 6 | ~400 MB installer | Acceptable for this class of app; torch/cv2/diffusers are gone |
| 7 | A user's render on a weak laptop could take hours | Surface an estimate in the UI (we already measure ~6.1x on 2 cores); warn before a 16:9 batch |

## 8. Decisions (answered by the owner, 2026-09-24)

| # | Question | Answer | Effect on the plan |
|---|---|---|---|
| 1 | Scope of "other people" | **Each user uses their own YouTube OAuth account**, not the owner's channel | Per-user BYOK OAuth is confirmed. **No central Google verification needed.** But step 3 of the 5-step setup (`Publishing status → In production`) is mandatory, or tokens die every 7 days. |
| 2 | Start where? | **The owner will build it themselves on their Mac** — hand over the repo, not a built artifact | Phases 1/2 stay as written; the owner drives them. Deliverable = repo access + this plan, not a finished `.dmg`. |
| 3 | macOS signing | **No Apple Developer fee** — right-click → Open is acceptable | Skip notarization. Document the Gatekeeper workaround in the install guide. Task 2.5 drops the signing step. |
| 4 | Windows | **The owner will handle it on their Mac later** | Keep the CI matrix in Task 2.5 (GitHub Actions can build Windows from a Mac push), but it is not a Phase-2 gate. macOS + Linux first. |

**Net effect:** the plan is unchanged in shape. Two things get simpler — no notarization, no central OAuth verification — and one thing becomes critical: the `In production` publishing-status step, because without it every user's connection breaks on day 8.

## 9. Suggested order

```
1. Decide #1–#4 above                                    (owner)
2. Phase 1: Dockerfile + APP_DIR/DATA_DIR split           (1–2 days)
3. Hand it to one outside user; fix what breaks           (the real test)
4. Phase 2: PyInstaller freeze  <- the risky step         (2–3 days)
5. Phase 2: bundled ffmpeg + output diff check            (1 day)
6. Phase 2: Tauri shell + first-run wizard (mockup first) (1 week)
7. Phase 2: CI matrix + signed release                    (2–3 days)
8. Start Google OAuth verification if going public        (weeks, parallel)
```
