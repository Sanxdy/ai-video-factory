#!/usr/bin/env bash
# AVF installer (spec §3). Idempotent — safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== AI Video Factory install ==="

# 1. Python venv
if [ ! -d .venv ]; then
    python3 -m venv .venv
    echo "[ok] created .venv"
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]" 2>/dev/null || .venv/bin/pip install -q -e .

# 2. System deps
for cmd in ffmpeg sqlite3; do
    if ! command -v "$cmd" >/dev/null; then
        echo "[MISSING] $cmd — install with: sudo apt install $cmd"
    else
        echo "[ok] $cmd"
    fi
done

# 4. Content dirs
mkdir -p content/{ideas,research,scripts,storyboards,assets,audio,subtitles,projects,rendered,approved,uploaded,music} \
         runtime/{logs,jobs,cache,locks} database
echo "[ok] directories"

echo "=== done. Run: .venv/bin/python -m apps.cli doctor ==="
