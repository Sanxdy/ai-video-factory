#!/usr/bin/env python3
"""Draw the AVF icon and emit every format the three platforms want.

    .venv/bin/python packaging/make_icon.py

Writes assets/icon/ (png, icns, ico), web/app/favicon.ico, and the PNG the
Windows shortcut and the Linux .desktop entry point at. Everything is derived
from one 1024px master, so the shapes cannot drift between platforms.

Design constraints come from design.md §17: legible at 16px, one idea, one
silhouette, no wordmark, no gradient mesh, no glow. So: a squircle tile in the
app accent, one near-black play triangle. That is the same mark the sidebar
draws, at icon scale. The contrast is deliberately extreme — a 16px triangle in
a muted colour turns to mush, and the accent tile is what makes it read.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "icon"

SIZE = 1024
SS = 4                      # supersample factor: PIL does not antialias, so draw
                            # big and let LANCZOS do the filtering
ACCENT = (43, 168, 162, 255)      # --color-accent (Flip7 Primary Teal)
INK = (16, 49, 47, 255)           # --color-on-accent

# Apple's icon grid: the artwork is an 824/1024 tile centred with 100px margins.
# The corners are superellipses, not circles, so rounded_rectangle would be
# visibly wrong at 512px. n=5 is the exponent Apple's shape works out to: the
# corner sits at 0.7071^(2/5)=0.8706 of the half-extent, a 0.1294a cut, which is
# the same cut a circular corner of radius 0.4418a = 22.1% of the tile gives.
TILE = 824
SQUIRCLE_N = 5.0


def squircle(cx: float, cy: float, half: float, n: float, steps: int = 720):
    """Polygon approximating a superellipse |x/a|^n + |y/a|^n = 1."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + half * math.copysign(abs(c) ** (2 / n), c),
                    cy + half * math.copysign(abs(s) ** (2 / n), s)))
    return pts


def master() -> Image.Image:
    px = SIZE * SS
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.polygon(squircle(px / 2, px / 2, TILE / 2 * SS, SQUIRCLE_N), fill=ACCENT)

    # Play triangle. 0.52 of the tile, not 0.46: at 16px the smaller triangle
    # filtered down to a grey smudge with no readable edge. Offset right by a
    # hair — a centred triangle looks like it is falling left, because the eye
    # weights the solid base.
    th = TILE * 0.52 * SS
    tw = th * 0.82
    cx, cy = px / 2 + tw * 0.09, px / 2
    d.polygon([(cx - tw / 2, cy - th / 2),
               (cx + tw / 2, cy),
               (cx - tw / 2, cy + th / 2)], fill=INK)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    img = master()
    img.save(OUT / "avf-1024.png")
    img.resize((512, 512), Image.LANCZOS).save(OUT / "avf-512.png")
    img.resize((256, 256), Image.LANCZOS).save(OUT / "avf-256.png")
    print(f"  png  {OUT.relative_to(REPO)}/avf-{{256,512,1024}}.png")

    ico = OUT / "avf.ico"
    img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print(f"  ico  {ico.relative_to(REPO)}")

    # Next.js serves app/favicon.ico at /favicon.ico with no code at all, which
    # is the one thing a browser asks for unprompted.
    shutil.copy2(ico, REPO / "web" / "app" / "favicon.ico")
    print("  ico  web/app/favicon.ico")

    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = OUT / "avf.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = base * scale
                if px > SIZE:
                    continue
                name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                img.resize((px, px), Image.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset),
                        "-o", str(OUT / "avf.icns")], check=True)
        shutil.rmtree(iconset, ignore_errors=True)
        print(f"  icns {OUT.relative_to(REPO)}/avf.icns")
    else:
        print("  icns skipped (macOS only; iconutil is part of the OS)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
