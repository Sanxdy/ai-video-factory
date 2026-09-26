.PHONY: test check demo ui rebuild-ui dev dev-scratch

test:
	.venv/bin/python -m pytest tests/ -q

check:
	.venv/bin/python scripts/system_check.py

demo:
	.venv/bin/python -m apps.orchestrator run-auto --dry-run

# ── dev loop ────────────────────────────────────────────────────────────────
# `make dev` is this repo's `npm run dev`: it serves the *built* UI straight
# out of the source checkout — same code a DMG would run, minus the bundled
# interpreter and ffmpeg, and with `web/out` rebuilt only when a UI source file
# is actually newer than the last build. No bundle, no DMG, ~1 s on a warm run.
#
# A source checkout resolves its data dir to the repo itself (core/config.py),
# so `dev` shows your real projects. Use `dev-scratch` when you want to poke at
# the UI without touching them.
HOST ?= 127.0.0.1
PORT ?= 8610

UI_SRCS := web/app web/next.config.js web/package.json
UI_STALE := $(shell find $(UI_SRCS) -type f -newer web/out/index.html 2>/dev/null | head -1)

ui:
	@if [ ! -f web/out/index.html ] || [ -n "$(UI_STALE)" ]; then \
	  echo "▶ web/out is stale — running npm run build…"; \
	  cd web && npm run build || exit 1; \
	else \
	  echo "▶ web/out is current — skipping rebuild"; \
	fi

rebuild-ui:
	cd web && npm run build

dev: ui
	@echo "▶ data dir: $$(.venv/bin/python -c 'from core.config import data_dir; print(data_dir())')"
	@echo "▶ console:  http://$(HOST):$(PORT)"
	.venv/bin/python -m apps.cli serve --host $(HOST) --port $(PORT)

dev-scratch: ui
	@export AVF_DATA_DIR=$$(mktemp -d); \
	echo "▶ scratch data: $$AVF_DATA_DIR (disposable — your real projects are untouched)"; \
	echo "▶ console:      http://$(HOST):$(PORT)"; \
	.venv/bin/python -m apps.cli serve --host $(HOST) --port $(PORT)
