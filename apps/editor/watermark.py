"""Watermark overlay — settings-driven, applied to every rendered video.

Settings keys (all optional, defaults shown):
  watermark.enabled   bool    true
  watermark.path      str     content/branding/watermark.png  ("" = disabled)
  watermark.x         float   0.72   normalized left edge (0..1 of width)
  watermark.y         float   0.04   normalized top edge  (0..1 of height)
  watermark.scale     float   0.14   width as fraction of frame width
  watermark.opacity   float   0.55   0..1

x/y are the TOP-LEFT corner in normalized coords — the Settings UI lets you
drag a preview handle and stores exactly these numbers, so ffmpeg needs no
position math beyond x*W / y*H.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.binaries import font_path
from core.config import data_dir
from core.logging import get_logger
from apps.editor.editor import FFMPEG, probe, run_ffmpeg

log = get_logger("watermark")

DEFAULTS = {"enabled": True, "path": "content/branding/watermark.png",
            "x": 0.72, "y": 0.04, "scale": 0.14, "opacity": 0.55}

RATIOS = ("16:9", "9:16")


def _ratio_key(ratio: str | None) -> str:
    return ratio if ratio in RATIOS else "9:16"


def _migrate_legacy(db):
    """Copy old single watermark settings into 9:16 (legacy default) once."""
    legacy = db.get_setting("watermark.x")
    if legacy is None:
        return
    migrated = db.get_setting("watermark.migrated")
    if migrated:
        return
    for k, default in DEFAULTS.items():
        old_val = db.get_setting(f"watermark.{k}")
        if old_val is None:
            old_val = default
        db.set_setting(f"watermark.9:16.{k}", str(old_val))
    db.set_setting("watermark.migrated", "1")


def get_watermark_settings(db, ratio: str | None = None) -> dict:
    _migrate_legacy(db)
    s = dict(DEFAULTS)
    rkey = _ratio_key(ratio)
    for k in DEFAULTS:
        v = db.get_setting(f"watermark.{rkey}.{k}")
        if v is None:
            continue
        if isinstance(DEFAULTS[k], bool):
            s[k] = str(v).lower() in ("1", "true", "yes")
        elif k == "path":
            s[k] = str(v)
        else:
            try:
                s[k] = max(0.0, min(1.0, float(v))) if k != "scale" \
                    else max(0.02, min(0.8, float(v)))
            except ValueError:
                pass
    return s


def apply_watermark(video: Path, out: Path, db, ratio: str | None = None) -> Path:
    """Overlay the configured watermark image onto `video` → `out`.

    Returns `out`, or the input unchanged when disabled/misconfigured —
    the pipeline never fails because of branding.
    """
    s = get_watermark_settings(db, ratio)
    wm = data_dir() / s["path"] if s["path"] else None
    if not s["enabled"] or not wm or not wm.exists():
        log.info("watermark disabled or missing (%s) — skipping", wm)
        _copy(video, out)
        return out

    info = probe(video)
    w, h = info["width"] or 1080, info["height"] or 1920
    target_w = max(24, round(w * s["scale"]))
    # clamp so the mark stays fully inside the frame regardless of drag pos
    px = round(min(max(s["x"], 0.0), 1.0) * w)
    py = round(min(max(s["y"], 0.0), 1.0) * h)

    vf = (
        f"[1:v]scale={target_w}:-1,format=rgba,"
        f"colorchannelmixer=aa={s['opacity']:.2f}[wm];"
        f"[0:v][wm]overlay=x={px}:y={py}:format=auto[v]"
    )
    run_ffmpeg([
        FFMPEG, "-y", "-i", str(video), "-i", str(wm),
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy", str(out),
    ])
    log.info("watermark applied: %s @(%d,%d) scale=%.2f op=%.2f",
             wm.name, px, py, s["scale"], s["opacity"])
    return out


def _copy(src: Path, dst: Path) -> None:
    import shutil
    shutil.copy2(src, dst)


def generate_default_watermark(path: Path | None = None) -> Path:
    """Create content/branding/watermark.png from the PiquedMind mark."""
    from PIL import Image, ImageDraw, ImageFont
    path = path or (data_dir() / "content" / "branding" / "watermark.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    W, H = 560, 160
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # mini bulb-spark: circle + zigzag + base
    lw = 9
    r = 56
    cx, cy = 80, 62
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(52, 211, 153, 255), width=lw)
    pts = [(cx - r * .42, cy + r * .35), (cx - r * .14, cy - r * .25),
           (cx + r * .02, cy + r * .23), (cx + r * .18, cy - r * .30),
           (cx + r * .42, cy + r * .35)]
    off_y = cy - 10
    pts = [(x, y - off_y + cy) for x, y in pts]
    d.line(pts, fill=(236, 72, 153, 255), width=lw - 3, joint="curve")
    d.line([cx - r * .52, cy + r + 6, cx - r * .52, cy + r + 34], fill=(52, 211, 153, 255), width=lw - 2)
    d.line([cx + r * .52, cy + r + 6, cx + r * .52, cy + r + 34], fill=(52, 211, 153, 255), width=lw - 2)
    d.rounded_rectangle([cx - r * .52, cy + r + 38, cx + r * .52, cy + r + 58],
                        radius=10, fill=(52, 211, 153, 255))
    font_file = font_path()
    f = (ImageFont.truetype(font_file, 64) if font_file
         else ImageFont.load_default())
    d.text((170, 40), "Piqued", font=f, fill=(255, 255, 255, 255))
    d.text((170 + d.textlength("Piqued", font=f), 40), "Mind",
           font=f, fill=(52, 211, 153, 255))
    img.save(path, "PNG")
    print(f"✓ {path}")
    return path
