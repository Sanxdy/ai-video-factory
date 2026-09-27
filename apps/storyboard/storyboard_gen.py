"""Storyboard engine (spec §12) — script → scenes."""
from __future__ import annotations

import re

from core.database import Database
from core.logging import get_logger
from core.prompts import render_prompt
from providers.llm import generate_parsed

from providers.tts.kokoro import narration_language
log = get_logger("storyboard")

# Silence held between ranked items in a long-form countdown. Without it the
# narration runs the sections together and the viewer loses track of which
# number they just heard (the "nyambung-nyambung" complaint). The last shot of
# each section is held for this long before the next item's number is spoken.
# ponytail: one constant for every section; make it per-project if a video ever
# wants a different rhythm.
COUNTDOWN_PAUSE = 1.8


def _split_evenly(text: str, n: int) -> list[str]:
    """Deterministic sentence split — fallback when the LLM summarizes."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    k = max(1, round(len(sents) / n))
    return [" ".join(sents[i:i + k]) for i in range(0, len(sents), k)]


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Parse '## heading' + narration blocks into (heading, narration) pairs.

    The long-form script writer emits one block per ranked item; the heading
    is a chapter label, never spoken.
    """
    out: list[tuple[str, str]] = []
    heading, cur = "", []
    for line in body.splitlines():
        if line.startswith("## "):
            if cur:
                out.append((heading, "\n".join(cur).strip()))
            heading, cur = line[3:].strip(), []
        elif line.strip():
            cur.append(line.strip())
    if cur:
        out.append((heading, "\n".join(cur).strip()))
    return [(h, t) for h, t in out if t]


VISUAL_BATCH = 12   # scenes per visual-prompt call — larger replies get truncated


def _split_into(text: str, n: int) -> list[str]:
    """Split text into ~n consecutive chunks without cutting mid-phrase.

    Each scene's narration is synthesised separately, so a cut in the middle
    of a phrase is audible. Sentence boundaries are preferred, then clause
    boundaries (comma/semicolon/colon) when the text has fewer sentences than
    scenes; word boundaries are the last resort.
    """
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    clauses = [c.strip() for c in re.split(r"(?<=[,;:])\s+", text) if c.strip()]
    parts = sents if len(sents) >= n else (clauses if len(clauses) > len(sents)
                                           else sents)
    if len(parts) >= 2:
        k = max(1, round(len(parts) / n))
        return [" ".join(parts[i:i + k]) for i in range(0, len(parts), k)]
    words = text.split()
    k = max(1, round(len(words) / max(1, n)))
    return [" ".join(words[i:i + k]) for i in range(0, len(words), k)]


def _visual_shots(chunks: list[tuple[str, str]]) -> list[dict]:
    """Camera description per scene, in batches the API model can complete."""
    shots: list[dict] = []
    for i in range(0, len(chunks), VISUAL_BATCH):
        batch = chunks[i:i + VISUAL_BATCH]
        listing = "\n".join(f"{j + 1}. [{h}] {t}" if h else f"{j + 1}. {t}"
                            for j, (h, t) in enumerate(batch))
        try:
            got = generate_parsed(
                "visuals", render_prompt("visuals", count=len(batch),
                                         listing=listing),
                temperature=0.6).model_dump()["shots"]
        except Exception as e:
            # a visual description is cosmetic — never fail the run over it
            log.warning("visual batch at %d failed (%s) — using narration", i, e)
            got = []
        for j, (_h, text) in enumerate(batch):
            s = got[j] if j < len(got) else {}
            shots.append({
                "visual_prompt": (s.get("visual_prompt") or text).strip(),
                "stock_query": (s.get("stock_query") or "").strip(),
                "motion_prompt": (s.get("motion_prompt") or "zoom-in").strip(),
            })
    return shots


def _longform_scenes(script: dict, scene_dur: float) -> list[dict]:
    """Scene list for a long-form script: deterministic split, LLM only for visuals.

    The Shorts storyboard asks the model to slice the script itself. At 60+
    scenes that reply is too large to trust and the model summarizes it, so
    here the split is deterministic (word-for-word by construction) and the
    model is only asked to describe the camera.
    """
    units: list[tuple[str, str]] = []
    if (script.get("hook") or "").strip():
        units.append(("", script["hook"]))
    units += _split_sections(script.get("body") or "")
    if (script.get("cta") or "").strip():
        units.append(("", script["cta"]))

    words_per_scene = max(4.0, scene_dur * 2.1)   # Kokoro pace ≈2.1 words/sec
    chunks: list[tuple[str, str]] = []
    pauses: list[float] = []
    for u_i, (heading, text) in enumerate(units):
        n = max(1, round(len(text.split()) / words_per_scene))
        pieces = [c.strip() for c in _split_into(text, n) if c.strip()]
        last_unit = u_i == len(units) - 1
        for j, c in enumerate(pieces):
            chunks.append((heading, c))
            # hold the last shot of every section (intro and each item, not the
            # outro) so the countdown breathes between numbers
            pauses.append(COUNTDOWN_PAUSE if (not last_unit and j == len(pieces) - 1) else 0.0)
    log.info("longform: %d sections → %d scenes @ %.1fs",
             len(units), len(chunks), scene_dur)

    shots = _visual_shots(chunks)
    return [{"scene_number": i + 1, "duration": scene_dur,
             "narration": text, "visual_prompt": s["visual_prompt"],
             "stock_query": s["stock_query"], "motion_prompt": s["motion_prompt"],
             "text_overlay": "", "asset_type": "image",
             "pause_after": pauses[i]}
            for i, ((_h, text), s) in enumerate(zip(chunks, shots))]


def _persist_scenes(db: Database, script_id: int, script: dict,
                    scenes: list[dict], append_cta: bool = True) -> list[int]:
    """Insert scene rows and close the script out. Shared by both paths.

    append_cta=False for long-form: there the cta is already the last scene,
    so appending it again duplicates the outro.
    """
    scene_ids = []
    for sc in scenes:
        # text_overlay is deprecated: it fights the active subtitle preset
        # and makes captions hard to read (project 114).
        sid = db.insert("scenes", script_id=script_id,
                        scene_number=sc["scene_number"], duration=sc["duration"],
                        narration=sc["narration"], visual_prompt=sc["visual_prompt"],
                        stock_query=sc.get("stock_query", ""),
                        motion_prompt=sc.get("motion_prompt", ""),
                        text_overlay="", pause_after=sc.get("pause_after", 0) or 0,
                        asset_type=sc.get("asset_type", "image"), status="PENDING")
        scene_ids.append(sid)
    # CTA: append to last scene's narration so it gets spoken + subtitled
    if append_cta and scene_ids and script.get("cta"):
        last_sid = scene_ids[-1]
        last_scene = db.get("scenes", last_sid)
        if last_scene:
            narration = (last_scene.get("narration") or "").rstrip()
            cta = script["cta"].strip()
            if cta and cta not in narration:
                narration = narration + ". " + cta
            db.update("scenes", last_sid, narration=narration, text_overlay="")
    db.update("scripts", script_id, status="STORYBOARDED")
    log.info("script %d: %d scenes", script_id, len(scene_ids))
    return scene_ids


def generate_storyboard(db: Database, script_id: int, project_id: int | None = None,
                        revision_note: str = "") -> list[int]:
    # idempotent: retry/resume must not duplicate scene rows
    existing = db.all("scenes", "script_id=? ORDER BY scene_number", (script_id,))
    if existing:
        log.info("script %d: %d scenes (existing)", script_id, len(existing))
        return [s["id"] for s in existing]
    script = db.get("scripts", script_id)
    if script is None:
        raise ValueError(f"script {script_id} not found")

    # read per-project max_scene_duration (set by /api/storyboard)
    max_dur = None
    if project_id:
        from core.settings import get_setting
        v = get_setting(f"project.{project_id}.max_scene_duration")
        if v:
            try:
                max_dur = max(1.0, float(v))
            except ValueError:
                pass

    # long-form: scene count comes from the duration budget, not from a
    # setting, and the split is deterministic (see _longform_scenes)
    from apps.scripting.script_gen import LONGFORM_MIN, _target_duration
    if _target_duration(project_id) >= LONGFORM_MIN:
        scenes = _longform_scenes(script, max_dur or 5.0)
        return _persist_scenes(db, script_id, script, scenes, append_cta=False)

    tpl = {
        "hook": script["hook"] or "",
        "body": script["body"] or "",
        "cta": script["cta"] or "",
        "duration": script["duration"] or 40,
        "language": narration_language() or "the same language as the narration (auto)",
        "scene_count": "6-8",
        "max_scene_duration": "8",
        "max_scene_duration_scene_words": "20",
    }
    # override with per-project settings if available
    if max_dur:
        tpl["max_scene_duration"] = str(int(max_dur))
        tpl["max_scene_duration_scene_words"] = str(max(15, int(max_dur * 2.5)))
    if project_id:
        from core.settings import get_setting as _gs
        sc = _gs(f"project.{project_id}.scene_count")
        if sc:
            tpl["scene_count"] = sc
    prompt = render_prompt("storyboard", **tpl)
    from apps.scripting.script_gen import revision_prompt
    prompt = revision_prompt(prompt, revision_note)
    out = generate_parsed("storyboard", prompt).model_dump()
    scenes = out["scenes"]

    # post-process: enforce exact scene count and duration
    target_count = int(tpl["scene_count"]) if tpl["scene_count"].isdigit() else len(scenes)
    target_dur = float(tpl["max_scene_duration"])

    # merge excess scenes down to target count
    while len(scenes) > target_count and len(scenes) > 1:
        # merge last two scenes
        a = scenes[-2]
        b = scenes[-1]
        a["narration"] = a["narration"].rstrip() + ". " + b["narration"].lstrip()
        a["duration"] = round(a["duration"] + b["duration"], 1)
        a["visual_prompt"] = a.get("visual_prompt") or b.get("visual_prompt", "")
        scenes = scenes[:-1]

    # renumber and set duration to target
    for i, s in enumerate(scenes):
        s["scene_number"] = i + 1
        s["duration"] = target_dur

    # guard: the model loves summarizing narration into title fragments —
    # if it dropped most of the body, slice it ourselves instead
    body_words = len((script["body"] or "").split())
    narr_words = sum(len(s["narration"].split()) for s in scenes)
    # guard 2 (project 55): the model also INFLATES the script with new
    # sentences (170 scene words vs 131 body words) — the extra words make
    # TTS run past the QC cap. Word-for-word means word-for-word both ways.
    inflated = body_words and narr_words > body_words * 1.10
    if inflated:
        log.warning("storyboard inflated script %d → %d words — even split fallback",
                    body_words, narr_words)
    if (body_words and narr_words < body_words * 0.7) or inflated:
        if not (narr_words < body_words * 0.7):
            log.warning("storyboard kept only %d/%d body words — even split fallback",
                        narr_words, body_words)
        fallback_count = target_count if tpl["scene_count"].isdigit() else min(8, max(5, len(scenes)))
        chunks = _split_evenly(script["body"], fallback_count)
        scenes = [{"scene_number": i + 1, "narration": c,
                   "duration": target_dur,
                   "visual_prompt": (scenes[i]["visual_prompt"]
                                     if i < len(scenes) and scenes[i].get("visual_prompt")
                                     else ""),
                   "motion_prompt": "zoom-in",
                   "text_overlay": "", "asset_type": "image"}
                  for i, c in enumerate(chunks)]
    # QC duration budget: render adds ~0.25s pad per scene and TTS can run
    # long, so check total doesn't exceed QC max. With fixed duration we do NOT
    # scale — warn instead so the user can reduce scene count or duration.
    from core.config import config as _cfg
    qc_max = (_cfg.get("quality", default={}) or {}).get("max_duration") or 75
    budget = qc_max - 1.0 - 0.4 * len(scenes)   # pad + safety margin
    total = sum(float(s.get("duration", 4.0)) for s in scenes)
    if total > budget:
        log.warning("storyboard total %.1fs exceeds QC budget %.1fs (fixed duration=%s, scenes=%d) — "
                    "may fail QC; reduce scene count or duration",
                    total, budget, tpl["max_scene_duration"], len(scenes))

    # Fixed duration: enforce EXACTLY the target duration on every scene
    if max_dur:
        for s in scenes:
            s["duration"] = round(max_dur, 1)
        log.info("enforced fixed scene duration: %.1fs", max_dur)

    # QC: detect duplicate visual prompts (project 246: all scenes got identical prompt)
    if len(scenes) > 1:
        vis_prompts = [s.get("visual_prompt", "")[:80].lower() for s in scenes]
        unique_vis = len(set(vis_prompts))
        if unique_vis < len(scenes):
            log.warning("storyboard has %d unique visual prompts across %d scenes — "
                        "LLM generated repetitive prompts", unique_vis, len(scenes))

    return _persist_scenes(db, script_id, script, scenes)
