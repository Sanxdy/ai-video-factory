#!/usr/bin/env bash
# AVF one-command bootstrap for macOS (Apple Silicon) & Linux.
# Usage:  git clone <repo> && cd ai-video-factory && ./scripts/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "╔═══════════════════════════════════════╗"
echo "║   AI Video Factory — bootstrap        ║"
echo "╚═══════════════════════════════════════╝"

# ── 1. system package manager ──────────────────────────────
PKG="apt"
if [[ "$(uname)" == "Darwin" ]]; then
    PKG="brew"
    if ! command -v brew >/dev/null; then
        echo "[setup] installing Homebrew…"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
fi

need() { command -v "$1" >/dev/null || echo "$2"; }

MISSING=""
command -v python3 >/dev/null || MISSING="$MISSING python3"
command -v ffmpeg   >/dev/null || MISSING="$MISSING ffmpeg"
command -v node     >/dev/null || MISSING="$MISSING node"
if [ -n "$MISSING" ]; then
    echo "[setup] installing system deps:$MISSING"
    if [ "$PKG" = "brew" ]; then
        brew install $MISSING
    else
        sudo apt update && sudo apt install -y $MISSING python3-venv
    fi
else
    echo "[ok] python3, ffmpeg, node"
fi

# ── 2. python venv + package ───────────────────────────────
# macOS system python3 is 3.9 — need ≥3.11, prefer brew-installed
PY=""
for p in python3.12 python3.11 python3.13 python3.14; do
    command -v "$p" >/dev/null && { PY="$p"; break; }
done
if [ -z "$PY" ]; then
    echo "[setup] no python ≥3.11 found — installing via ${PKG}…"
    if [ "$PKG" = "brew" ]; then brew install python@3.12; else sudo apt install -y python3.11 python3.11-venv; fi
    for p in python3.12 python3.11 python3.13 python3.14; do
        command -v "$p" >/dev/null && { PY="$p"; break; }
    done
fi
[ -n "$PY" ] || { echo "[error] python ≥3.11 required — install it and rerun"; exit 1; }
echo "[ok] using $PY ($("$PY" --version))"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null; then
    rm -rf .venv
    "$PY" -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
echo "[setup] installing python dependencies…"
.venv/bin/pip install -q -e .
echo "[ok] venv ready"

# ── 4. web frontend ────────────────────────────────────────
echo "[setup] building web UI (npm, one-time ~1 min)…"
( cd web && npm install --silent && npm run build --silent ) \
    && echo "[ok] web UI built" || echo "[warn] web build failed — CLI still works"

# ── 5. content/runtime dirs ────────────────────────────────
mkdir -p content/{ideas,research,scripts,storyboards,assets,audio,subtitles,projects,rendered,approved,uploaded,music} \
         runtime/{logs,jobs,cache,locks} database
echo "[ok] directories"

# ── 6. local AI models (kokoro TTS + whisper STT auto-download) ──
echo "[setup] pre-fetching Kokoro TTS + Whisper models (~600 MB, one-time)…"
.venv/bin/python scripts/download_models.py 2>/dev/null | tail -4 || true

# ── 7. health check ────────────────────────────────────────
echo ""
echo "═══ Doctor ═══"
.venv/bin/python -m apps.cli doctor 2>/dev/null | tail -20 || true

cat << 'EOF'

╔══════════════════════════════════════════════════╗
║  Done! Next steps:                               ║
║                                                  ║
║  source .venv/bin/activate                       ║
║  avf produce run            # make first video   ║
║  avf serve                  # web UI :8600       ║
║                                                  ║
║  YouTube upload (once):                          ║
║    copy client_secret.json → runtime/            ║
║    avf youtube auth                              ║
╚══════════════════════════════════════════════════╝
EOF
