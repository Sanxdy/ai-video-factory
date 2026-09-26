"""Locate ffmpeg / ffprobe / a bold font — one resolver for the whole codebase.

A packaged bundle ships its own copies under `vendor/` (read-only, beside the
code); a source checkout falls back to PATH. ffmpeg is chosen by capability,
not mere presence: brew's plain build has no libass/libfreetype, so `subtitles`
and `drawtext` produce silently broken renders. Hence the -filters probe.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from core.config import ROOT, data_dir

_EXE = ".exe" if os.name == "nt" else ""
_FILTERS: dict[str, str] = {}

_FONTS = (
    ROOT / "vendor" / "fonts" / "DejaVuSans-Bold.ttf",              # bundled
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),   # Linux
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),      # macOS
    Path("/System/Library/Fonts/Helvetica.ttc"),
)


def _filters(binary: str) -> str:
    """`ffmpeg -filters` output, cached; '' when the binary cannot run."""
    if binary not in _FILTERS:
        try:
            r = subprocess.run([binary, "-hide_banner", "-filters"],
                               capture_output=True, text=True, timeout=20)
            _FILTERS[binary] = r.stdout
        except Exception:
            _FILTERS[binary] = ""
    return _FILTERS[binary]


def _find(name: str, feature: str | None = None) -> str:
    """Explicit override, then bundled, then the known full builds, then PATH."""
    cands = [Path(os.environ.get(f"AVF_{name.upper()}") or "."),
             ROOT / "vendor" / f"{name}{_EXE}",
             Path("/opt/homebrew/opt/ffmpeg-full/bin") / f"{name}{_EXE}",
             Path("/usr/local/opt/ffmpeg-full/bin") / f"{name}{_EXE}"]
    found = [c for c in cands if c.is_file()]
    if (p := shutil.which(name)):
        found.append(Path(p))
    if not found:
        return name  # let the OS resolve it at exec time
    if feature:
        return next((str(c) for c in found if feature in _filters(str(c))),
                    str(found[0]))
    return str(found[0])


def ffmpeg_path(feature: str | None = "subtitles") -> str:
    """ffmpeg carrying `feature` in -filters when any candidate has it."""
    return _find("ffmpeg", feature)


def ffprobe_path() -> str:
    return _find("ffprobe")


def has_filter(binary: str, feature: str) -> bool:
    """Whether `binary`'s -filters lists `feature` (cached probe)."""
    return feature in _filters(binary)


def font_path() -> str | None:
    """Bold font file for drawtext, bundled copy first."""
    return next((str(f) for f in _FONTS if f.is_file()), None)


def configure_fontconfig() -> str | None:
    """Point fontconfig at the bundled font when the host has no config of its own.

    drawtext takes an explicit `fontfile`, but the `subtitles` filter does not —
    libass resolves FontName through fontconfig. ffmpeg links fontconfig
    statically but not its configuration, so on a host without
    /etc/fonts/fonts.conf libass draws *nothing* and ffmpeg still exits 0: a
    silent failure, and the kind that ships unnoticed. Every desktop distro has
    the file, so this is a no-op there; it matters for minimal images.

    Returns the config path when one was written, else None.
    """
    if not sys.platform.startswith("linux"):
        return None          # macOS/Windows libass use CoreText/DirectWrite
    if os.environ.get("FONTCONFIG_FILE") or Path("/etc/fonts/fonts.conf").exists():
        return None
    fonts = ROOT / "vendor" / "fonts"
    if not fonts.is_dir():
        return None
    try:
        conf = data_dir() / "runtime" / "fonts.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
            "<fontconfig>\n"
            f"  <dir>{fonts}</dir>\n"
            f"  <cachedir>{conf.parent / 'fontconfig-cache'}</cachedir>\n"
            "</fontconfig>\n")
    except OSError:
        return None
    os.environ["FONTCONFIG_FILE"] = str(conf)
    return str(conf)


configure_fontconfig()
