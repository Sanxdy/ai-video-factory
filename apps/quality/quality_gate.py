"""Quality control (spec §22) — automated review before approval/upload.

Checks (deterministic + LLM-assisted):
- duration within limits
- resolution == 1080x1920
- has audio track (reject silent)
- has subtitles when required
- audio not clipped (loudness sanity)
- LLM content review (hook strength, flow, facts)
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.binaries import ffmpeg_path
from core.config import config
from core.logging import get_logger
from providers.llm import generate_parsed
from apps.editor.editor import probe

log = get_logger("quality")

# A 16:9 render is a long-form video, and the user asked for "at least 5
# minutes, never less". Shorts keep their own min/max from settings.
LONG_MIN_DURATION = 300.0


class QualityGate:
    def __init__(self, video_path=None, subtitles_path=None, script=None,
                 aspect_ratio: str | None = None):
        self.video_path = video_path
        self.subtitles_path = subtitles_path
        self.script = script or {}
        self.aspect_ratio = aspect_ratio

    # ── deterministic checks ───────────────────────────
    def check_technical(self) -> list[str]:
        issues = []
        q = config.get("quality", default={}) or {}
        if self.video_path is None:
            issues.append("no video path provided")
            return issues
        info = probe(self.video_path)
        # long-form (16:9) projects render 1920x1080; everything else is Shorts
        want = (1920, 1080) if self.aspect_ratio == "16:9" else (1080, 1920)
        if (info["width"], info["height"]) != want:
            issues.append(f"resolution {info['width']}x{info['height']} ≠ {want[0]}x{want[1]}")
        # long-form legitimately exceeds the Shorts duration cap
        is_long = self.aspect_ratio == "16:9"
        dur = info["duration"]
        if not is_long and q.get("max_duration") and dur > q["max_duration"]:
            issues.append(f"duration {dur:.1f}s > max {q['max_duration']}s")
        if not is_long and q.get("min_duration") and dur < q["min_duration"]:
            issues.append(f"duration {dur:.1f}s < min {q['min_duration']}s")
        # long-form floor: never publish a landscape video under 5 minutes
        if is_long and dur < LONG_MIN_DURATION:
            issues.append(f"long-form duration {dur:.1f}s < "
                          f"{LONG_MIN_DURATION:.0f}s")
        if q.get("reject_missing_audio") and not info["has_audio"]:
            issues.append("no audio track")
        if q.get("reject_missing_subtitles") and self.subtitles_path \
                and not self.subtitles_path.exists():
            issues.append("subtitles file missing")
        issues += self._check_frames()
        issues += self._check_silence()
        return issues

    def _placeholder_scenes(self) -> list[int]:
        """Scene numbers rendered from a procedural gradient, not real footage.

        The editor writes `source` per timeline entry into manifest.json next to
        the video. A gradient is a smooth ramp, so a slow Ken Burns zoom moves
        too few pixels per frame to clear freezedetect — the render then fails
        with "frozen frames", which reads like an encoder bug and hides the
        actual cause (the stock lookup returned nothing for that scene).
        """
        import json
        mf = Path(self.video_path).parent / "manifest.json"
        if not mf.is_file():
            return []
        try:
            tl = json.loads(mf.read_text()).get("timeline") or []
        except (OSError, ValueError):
            return []
        return [t["scene"] for t in tl if t.get("source") == "gradient"]

    def _check_frames(self) -> list[str]:
        """Black-frame & frozen-frame detection (spec §21)."""
        issues = []
        video = str(self.video_path)
        # black frames: pixel counts as black if luma < 10% (default pix_th);
        # segment must last >0.5s to count
        r = subprocess.run(
            [ffmpeg_path(), "-i", video, "-vf", "blackdetect=d=0.5",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        blacks = re.findall(r"black_start:([\d.]+).*black_end:([\d.]+)",
                            r.stderr)
        total_black = sum(float(e) - float(s) for s, e in blacks)
        dur = probe(self.video_path)["duration"] or 1
        if total_black > dur * 0.3:
            issues.append(f"excessive black frames ({total_black:.1f}s)")

        # frozen frames: static picture signal >2s. Threshold scales with
        # length — 3 freezes in a 60s Short is a defect, 3 in a 6-min video is not.
        r = subprocess.run(
            [ffmpeg_path(), "-i", video, "-vf", "freezedetect=n=0.001:d=2",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        freezes = len(re.findall(r"freeze_start", r.stderr))
        if freezes >= max(3, round(dur / 60 * 3)):
            placeholders = self._placeholder_scenes()
            if placeholders:
                shown = ", ".join(str(n) for n in placeholders[:8])
                more = f" (+{len(placeholders) - 8} more)" if len(placeholders) > 8 else ""
                issues.append(
                    f"{len(placeholders)} scene(s) have no stock footage and "
                    f"rendered from a placeholder gradient — scene {shown}{more}. "
                    f"Nothing to watch there; check the stock API key/quota in "
                    f"Settings → Media, or import your own clips")
            else:
                issues.append(f"frozen frames detected ({freezes} segments)")
        return issues

    def _check_silence(self) -> list[str]:
        """Excessive silence in the audio track (spec §21)."""
        issues = []
        r = subprocess.run(
            [ffmpeg_path(), "-i", str(self.video_path), "-af",
             "silencedetect=noise=-40dB:d=3", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        silences = re.findall(r"silence_start:([\d.]+)", r.stderr)
        total = 0.0
        for m in re.finditer(r"silence_start:([\d.]+).*?silence_end:([\d.]+)",
                             r.stderr, re.S):
            total += float(m.group(2)) - float(m.group(1))
        dur = probe(self.video_path)["duration"] or 1
        if total > dur * 0.5:
            issues.append(f"excessive silence ({total:.1f}s of {dur:.1f}s)")
        return issues

    # ── LLM content review ─────────────────────────────
    def review_content(self) -> dict:
        """LLM review of script quality. Returns {score, pass, issues, recs}."""
        from providers.llm import generate_parsed
        from core.prompts import render_prompt

        scenes = self.script.get("scenes", [])
        prompt = render_prompt("quality", hook=self.script.get("hook", ""),
                               body=self.script.get("body", ""),
                               cta=self.script.get("cta", ""),
                               scenes=scenes)
        return generate_parsed("quality", prompt, temperature=0.2).model_dump(by_alias=True)

    # ── full gate ──────────────────────────────────────
    def evaluate(self, llm_review: bool = True) -> dict:
        issues = self.check_technical()
        review = {}
        if llm_review and self.script:
            try:
                review = self.review_content()
            except Exception as e:
                issues.append(f"LLM review failed: {e}")
                review = {"score": 0, "pass": False, "issues": [str(e)], "recommendations": []}

        score = review.get("score", 100)
        if issues:
            score = min(score, 60)  # technical failure caps score
        passed = score >= 60 and not issues and review.get("pass", True)

        return {
            "score": score,
            "passed": passed,
            "technical_issues": issues,
            "review": review,
        }


def evaluate_video(video_path, subtitles_path=None, script=None) -> dict:
    gate = QualityGate(video_path, subtitles_path, script)
    return gate.evaluate()