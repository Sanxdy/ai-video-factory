"""Image generation provider (spec §15, §8) — switchable, fallback-aware.

Providers (settings key `image.provider`):
  auto     — best available: pexels(key) → gradient
  pexels   — free stock-photo API, needs API key (BYOK)

Pollinations was removed as a tier: it sent the scene prompt off the machine and
could not be verified here (2 of 5 requests returned HTTP 500; the successes came
back 576x1024 with a watermark when asked for 1080x1920). `image_provider_setting`
maps a stored value naming it back to `auto`.

`gradient` is not offered in the UI — it is the terminal fallback that keeps
`generate()` from raising when Pexels has no key or the request fails.

Whatever the source, output is normalized to 1080x1920.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Literal

import httpx

from core.binaries import ffmpeg_path
from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("image")

# "1080x1920" (Shorts) | "1920x1080" (long-form 16:9)
Resolution = str

PROVIDERS = ("auto", "pexels")


def image_provider_setting() -> str:
    """Stored provider, coerced to one this module can serve.

    A stored value can outlive the provider it names — pollinations was removed
    — and an unrecognised one matches no tier in `_chain`, which would fail every
    scene instead of degrading.
    """
    from core.settings import get_setting
    val = get_setting("image.provider") or "auto"
    return val if val in PROVIDERS else "auto"


class ImageProvider:
    def __init__(self, resolution: Resolution = "1080x1920"):
        self.resolution = resolution
        self.w, self.h = (int(x) for x in resolution.split("x"))
        # Which tier served the last generate(). "gradient" means the scene has
        # no real footage — the editor records it in the manifest so the QC gate
        # can say so instead of blaming frozen frames on the encoder.
        self.last_tier: str | None = None

    # ── availability ────────────────────────────────────
    def _pexels_key(self) -> str:
        from core.settings import get_setting
        return get_setting("image.pexels_key") or ""

    def _chain(self, prefer: str | None) -> list[str]:
        """Provider order: preferred first, gradient always last."""
        mode = prefer or image_provider_setting()
        if mode == "pexels":
            return ["pexels", "gradient"]
        if mode == "gradient":
            return ["gradient"]
        # auto: the best configured source, then the $0 fallback
        return (["pexels"] if self._pexels_key() else []) + ["gradient"]

    # ── main API ────────────────────────────────────────
    def generate(self, prompt: str, out_path: Path,
                 prefer: str | None = None) -> Path:
        """Generate an image with the configured provider, fall back on failure."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        errors = []
        self.last_tier = None
        for tier in self._chain(prefer):
            try:
                if tier == "pexels":
                    out = self._gen_pexels(prompt, out_path)
                elif tier == "gradient":
                    out = self._gen_gradient(prompt, out_path)
                else:
                    continue
                self.last_tier = tier
                return out
            except Exception as e:
                errors.append(f"{tier}: {e}")
                log.warning("image tier %s failed, falling through: %s", tier, e)
        raise ProviderError("all image providers failed: " + "; ".join(errors))

    # ── shared helpers ──────────────────────────────────
    def _orientation(self) -> str:
        """Pexels/stock orientation matching the frame: portrait vs landscape."""
        return "landscape" if self.w > self.h else "portrait"

    def _fit(self, src: Path, out_path: Path) -> Path:
        """Scale + center-crop any source to the target resolution."""
        cmd = [
            ffmpeg_path(), "-y", "-i", str(src),
            "-vf", (f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,"
                    f"crop={self.w}:{self.h}"),
            "-frames:v", "1", str(out_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise ProviderError(f"ffmpeg fit failed: {r.stderr[-200:]}")
        return out_path

    def _seed(self, prompt: str) -> int:
        return int(hashlib.sha256(prompt.encode()).hexdigest()[:6], 16)

    # ── pexels (stock, BYOK) ────────────────────────────
    def _gen_pexels(self, prompt: str, out_path: Path) -> Path:
        key = self._pexels_key()
        if not key:
            raise ProviderError("no Pexels API key configured")
        query = " ".join(prompt.split()[:6]) or "abstract background"
        r = httpx.get("https://api.pexels.com/v1/search",
                      params={"query": query, "per_page": 3,
                              "orientation": self._orientation()},
                      headers={"Authorization": key}, timeout=30)
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if not photos:
            raise ProviderError(f"no Pexels results for '{query}'")
        pick = photos[self._seed(prompt) % len(photos)]
        img = httpx.get(pick["src"]["large2x"], timeout=60, follow_redirects=True)
        img.raise_for_status()
        tmp = out_path.with_suffix(".raw.img")
        tmp.write_bytes(img.content)
        return self._fit(tmp, out_path)

    # ── gradient ($0 fallback) ──────────────────────────
    def _gen_gradient(self, prompt: str, out_path: Path) -> Path:
        """FFmpeg procedural two-tone vertical gradient, hue derived from prompt."""
        import colorsys
        hue = self._seed(prompt) % 360
        hue2 = (hue + 40) % 360
        c0 = colorsys.hsv_to_rgb(hue / 360, 0.55, 0.35)
        c1 = colorsys.hsv_to_rgb(hue2 / 360, 0.7, 0.25)
        r0, g0, b0 = (int(x * 255) for x in c0)
        r1, g1, b1 = (int(x * 255) for x in c1)
        expr = (
            f"r='{r0}+({r1}-{r0})*Y/{self.h}':"
            f"g='{g0}+({g1}-{g0})*Y/{self.h}':"
            f"b='{b0}+({b1}-{b0})*Y/{self.h}'"
        )
        cmd = [
            ffmpeg_path(), "-y",
            "-f", "lavfi", "-i",
            f"color=c=black:s={self.w}x{self.h}:d=1,format=rgb24,geq={expr}",
            "-frames:v", "1", str(out_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise ProviderError(f"ffmpeg bg failed: {r.stderr[-200:]}")
        return out_path


def generate_scene_image(scene: dict, out_path: Path) -> Path:
    """Generate image for a storyboard scene (prompt-aware)."""
    prov = ImageProvider()
    prompt = scene.get("visual_prompt", "") or scene.get("narration", "abstract")
    log.info("image gen: provider=%s", prov._chain(None)[0])
    return prov.generate(prompt, out_path)
