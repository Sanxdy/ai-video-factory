#!/usr/bin/env python3
"""System inspection for AI Video Factory.

Detects hardware/software capabilities and saves to runtime/system-info.json.
Platform-agnostic (works on macOS Apple Silicon and Linux ARM/x86).
"""
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(parents=True, exist_ok=True)

def run(cmd: list[str], timeout: int = 15) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip() or None
    except Exception:
        return None

def check_tool(name: str) -> dict:
    found = shutil.which(name)
    if not found:
        return {"installed": False}
    ver = run([name, "--version"]) if name in ("python3", "node", "git", "docker") else run([name, "-version"] if name == "ffmpeg" else [name, "--version"])
    return {"installed": True, "path": found, "version": (ver or "").split("\n")[0][:120]}

info = {
    "os": {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "macos_version": run(["sw_vers", "-productVersion"]) if platform.system() == "Darwin" else None,
    },
    "arch": platform.machine(),
    "machine": platform.machine(),
    "cpu_count": (os_cpus := None),
    "ram_bytes": None,
    "disk_free_bytes": None,
    "tools": {t: check_tool(t) for t in ["python3", "node", "ffmpeg", "docker", "git", "git-lfs"]},
    "python": {
        "version": sys.version.split()[0],
        "executable": sys.executable,
    },
    "mlx_available": None,
    "cuda_available": None,
}

import os
try:
    import os as _os
    info["cpu_count"] = _os.cpu_count()
except Exception:
    pass

try:
    meminfo = Path("/proc/meminfo").read_text().splitlines()
    for line in meminfo:
        if line.startswith("MemTotal:"):
            info["ram_bytes"] = int(line.split()[1]) * 1024
            break
except Exception:
    try:
        r = run(["sysctl", "-n", "hw.memsize"])
        if r:
            info["ram_bytes"] = int(r)
    except Exception:
        pass

try:
    s = shutil.disk_usage(str(ROOT))
    info["disk_free_bytes"] = s.free
except Exception:
    pass

# MLX check (Apple Silicon only)
if platform.system() == "Darwin" and platform.machine() == "arm64":
    mod = run([sys.executable, "-c", "import mlx.core; print('ok')"])
    info["mlx_available"] = bool(mod) and "ok" in mod
    info["metal_gpu"] = run(["system_profiler", "SPDisplaysDataType"]) or None

# CUDA check (NVIDIA only)
try:
    nv = run(["nvidia-smi"])
    info["cuda_available"] = bool(nv) and "NVIDIA" in (nv or "")
except Exception:
    pass

# Human-readable summary
def human(n: int | None) -> str:
    if not n:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

info["_summary"] = {
    "os": f"{info['os']['system']} {info['os']['release']}",
    "arch": info["arch"],
    "cpu_cores": info["cpu_count"],
    "ram": human(info["ram_bytes"]),
    "disk_free": human(info["disk_free_bytes"]),
    "python": info["python"]["version"],
    "ffmpeg": info["tools"]["ffmpeg"].get("version", "missing"),
}

out = RUNTIME / "system-info.json"
out.write_text(json.dumps(info, indent=2, default=str))
print(json.dumps(info["_summary"], indent=2))
print(f"\nSaved to {out}")