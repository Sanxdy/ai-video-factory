"""`avf doctor` — environment & pipeline readiness (spec §42 diagnostics)."""
from __future__ import annotations

import json
import shutil

from core.config import data_dir
from core.model_manager import discover_models, verify_model
from core.logging import get_logger

log = get_logger("doctor")


def _check(name: str, ok: bool, detail: str = "") -> tuple[bool, str]:
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok, name


def run_doctor() -> bool:
    print("=== AI Video Factory doctor ===")
    all_ok = True

    # python
    import sys
    all_ok &= _check("python", sys.version_info >= (3, 11), sys.version.split()[0])[0]

    # ffmpeg/ffprobe — name the resolved build and the filters the renderer needs
    from pathlib import Path
    from core.binaries import ffmpeg_path, ffprobe_path, has_filter
    ff = ffmpeg_path()
    gaps = [f for f in ("subtitles", "drawtext") if not has_filter(ff, f)]
    all_ok &= _check("ffmpeg", bool(shutil.which(ff) or Path(ff).is_file()) and not gaps,
                     ff + (f" — missing filters: {', '.join(gaps)}" if gaps else ""))[0]
    fp = ffprobe_path()
    all_ok &= _check("ffprobe", bool(shutil.which(fp) or Path(fp).is_file()), fp)[0]

    # content dirs
    from core.filesystem import ensure_dirs
    ensure_dirs()
    all_ok &= _check("content dirs", True, "created if missing")[0]

    # database
    try:
        from apps.orchestrator.pipeline import get_db
        db = get_db()
        n = len(db.all("projects"))
        all_ok &= _check("database", True, f"{n} projects")[0]
    except Exception as e:
        all_ok &= _check("database", False, str(e))[0]

    # llm provider — BYOK API only (no local fallback by design)
    try:
        from providers.llm import active_model, ensure_llm_ready
        from core.settings import get_setting
        ensure_llm_ready()
        all_ok &= _check("llm", True,
                         f"api/{active_model()} @ {get_setting('llm.api_base_url')}")[0]
    except Exception as e:
        all_ok &= _check("llm", False, str(e))[0]

    # models — primary missing = FAIL, fallback missing = WARN (it's optional)
    for m in discover_models():
        if m.get("status") == "disabled":
            continue
        ok = verify_model(m["name"])
        if ok:
            all_ok &= _check(f"model {m['name']}", True, m["backend"])[0]
        elif m.get("status") == "fallback":
            _check(f"model {m['name']} (fallback, optional)", True,
                   m["backend"] + " — not installed, OK")
        else:
            all_ok &= _check(f"model {m['name']}", False, m["backend"])[0]

    # storage
    free = shutil.disk_usage(data_dir()).free / (1024**3)
    all_ok &= _check("disk space", free > 10, f"{free:.1f} GB free")[0]

    print("=== " + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED") + " ===")
    return all_ok


if __name__ == "__main__":
    run_doctor()