"""Hook + script generators (spec §10-11)."""
from __future__ import annotations

import json
from datetime import datetime

from core.database import Database
from core.prompts import render_prompt
from providers.llm import generate_parsed
from providers.tts.kokoro import narration_language
from core.logging import get_logger

log = get_logger("scripting")


def generate_hooks(db: Database, idea_id: int) -> dict:
    idea = db.get("ideas", idea_id)
    res = db.all("research", "idea_id=?", (idea_id,))
    research = json.loads(res[0]["facts"]) if res else []

    prompt = render_prompt(
        "hooks", topic=idea["topic"],
        summary=json.dumps(research[:3]) if research else "",
        language=narration_language(),
    )
    out = generate_parsed("hooks", prompt, temperature=0.7).model_dump()
    # store best hook to the idea and return all
    best = max(out["hooks"], key=lambda h: h["score"])
    db.update("ideas", idea_id, hook=best["text"], status="HOOKED")
    log.info("idea %d: best hook score=%d", idea_id, best["score"])
    return out


def _target_duration(project_id: int | None) -> float:
    """Long-form duration budget in seconds; 0.0 when this is a Short."""
    if not project_id:
        return 0.0
    from core.settings import get_setting
    try:
        return float(get_setting(f"project.{project_id}.target_duration") or 0)
    except (TypeError, ValueError):
        return 0.0


# at or above this many seconds a project takes the long-form path
LONGFORM_MIN = 120

# Kokoro af_heart's real pace, measured on project 290 (67 scenes, 615 spoken
# words, 196.8s of speech): ~3.1 words/sec. The Shorts estimator deliberately
# keeps 2.1 — it can only over-estimate, which keeps a Short under the 75s QC
# cap. Long-form budgets must use the measured pace or the script lands ~33%
# short (a 330s target produced a 221s video).
LONGFORM_WPS = 3.1

# Words per second of FINISHED video — which is not the speech pace. The
# finished runtime is speech + 0.25s of pad per scene + a 1.8s countdown hold
# per section, so a budget expressed in finished seconds needs fewer words than
# LONGFORM_WPS implies. Calibrated on the two measured long-form runs:
#   project 290:  638 words -> 213.6s (2.99 words/s)
#   project 295: 1261 words -> 440.0s (2.87 words/s)
# Budgeting at 3.1 overshoots: a 360s target produced a 440s video (+22%).
LONGFORM_VIDEO_WPS = 2.9

INTRO_SECS, OUTRO_SECS = 30.0, 22.0

# Hard floor for a long-form video: the user asked for "at least 5 minutes,
# never less". Enforced twice — the script budget below aims above it, and the
# quality gate fails a render that still comes out short.
LONGFORM_MIN_SECONDS = 300.0
# The model delivers ~89% of the words it is asked for (a 693-word request
# produced 615 words on the first long-form run), so budget well above the
# floor: 300s * 1.2 = 360s requested -> ~5.3 min even after the shortfall.
LONGFORM_TARGET_SLACK = 1.2


def _max_words(project_id: int | None) -> int:
    """Spoken-word budget for the script body.

    Shorts keep the 150-word default (≈71s, margin under the 75s QC cap).
    A long-form project raises it by setting project.<pid>.target_duration.
    """
    t = _target_duration(project_id)
    return max(30, round(t / 2.1)) if t else 150


# Number words a topic may use to state its own count ("5 Places ...").
_COUNT_WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8}


def _topic_item_count(topic: str) -> int | None:
    """Item count the topic states for itself ("6 Places ..."), else None.

    The model numbers its own items, and with no rank given it follows the
    number in the topic: a 5-item list on a "6 Places" topic came back numbered
    six..two and never reached number one. Read the topic's own number and make
    it the real count instead of letting the model guess.
    """
    import re
    m = re.match(r"\s*(\d+|three|four|five|six|seven|eight)\b",
                 (topic or "").lower())
    if not m:
        return None
    tok = m.group(1)
    return int(tok) if tok.isdigit() else _COUNT_WORDS.get(tok)


def _countdown_items(target: float, topic: str | None = None) -> int:
    """How many ranked items the countdown has.

    A number in the topic wins — the intro announces it and the outro refers
    back to it, so disagreeing with the topic is audible. Otherwise derive from
    duration at ~66s per item (narration plus a share of intro and outro).

    ponytail: clamped to 3..8. Expose as a setting if a video ever needs a
    different count.
    """
    stated = _topic_item_count(topic) if topic else None
    if stated:
        return max(3, min(8, stated))
    return max(3, min(8, int(target / 66) or 3))


def _rank_note(kind: str, rank: int | None, count: int) -> str:
    """The explicit rank instruction handed to the model for one section.

    Passing the number is what makes the countdown land on number one; leaving
    the model to infer it is what produced a six..two countdown.
    """
    if kind == "intro":
        return (f"This section is the HOOK, not a summary of the video. The "
                f"video counts down {count} items, from number {count} down "
                f"to number one. Say how many there are, tease that number "
                f"one is the most extreme of all without revealing it, and "
                f"end by starting the countdown out loud at number {count}.")
    if kind == "outro":
        return ""
    return (f"This section is ranked item number {rank} of {count}. The "
            f"countdown runs {count} down to 1, so use exactly this number.")


def _cta_examples(db: Database, limit: int = 3) -> str:
    """Recent Shorts calls-to-action, quoted to the outro prompt as style.

    The Shorts cta is a direct question to the viewer ("Which of those records
    surprised you most? Tell me in the comments.") and it works. Long-form
    outros had drifted into a flat "thank you for watching" because the prompt
    never asked for the question — showing the model real examples is the
    cheapest way to get that shape back without hardcoding one line.
    """
    try:
        rows = db.all("scripts", "duration < 120 AND cta IS NOT NULL "
                                 "AND cta <> '' ORDER BY id DESC LIMIT 40")
    except Exception:
        return "(none available)"
    seen, out = set(), []
    for r in rows:
        cta = (r.get("cta") or "").strip()
        key = cta.lower()
        if len(cta) > 25 and key not in seen:
            seen.add(key)
            out.append(cta)
        if len(out) >= limit:
            break
    return " | ".join(out) if out else "(none available)"


def generate_longform_script(db: Database, idea: dict, facts: str,
                             target: float, project_id: int | None) -> int:
    """Countdown script, ONE LLM CALL PER SECTION.

    A single call for 700+ spoken words comes back truncated or summarized by
    the API model, so each section is generated on its own with the sections
    already written passed in as "do not repeat". The countdown shape is the
    point: ranked items give the viewer a reason to keep watching.

    The duration budget is raised to the long-form floor before anything is
    sized, so a project that asked for a shorter video still produces one that
    meets the 5-minute minimum.
    """
    if target < LONGFORM_MIN_SECONDS * LONGFORM_TARGET_SLACK:
        target = LONGFORM_MIN_SECONDS * LONGFORM_TARGET_SLACK
        log.info("long-form budget raised to %.0fs (floor %.0fs)",
                 target, LONGFORM_MIN_SECONDS)
    items = _countdown_items(target, idea.get("topic"))
    kinds = ["intro"] + [f"item {i}" for i in range(1, items + 1)] + ["outro"]
    # The budget is in FINISHED seconds, so use the finished-video rate; the
    # intro/outro are expressed as speech seconds, so those use the speech rate.
    total_w = round(target * LONGFORM_VIDEO_WPS)
    intro_w = round(INTRO_SECS * LONGFORM_WPS)
    outro_w = round(OUTRO_SECS * LONGFORM_WPS)
    item_w = max(60, round((total_w - intro_w - outro_w) / items))
    per = {k: (intro_w if k == "intro" else
               outro_w if k == "outro" else item_w) for k in kinds}

    # Numbering runs DOWN (item 1 is the highest rank), so the last item is
    # number one — the payoff the whole countdown is built around.
    ranks = {f"item {i}": items + 1 - i for i in range(1, items + 1)}
    cta_examples = _cta_examples(db)

    parts, headings, written = [], [], []
    for i, kind in enumerate(kinds):
        prompt = render_prompt(
            "longform", topic=idea["topic"], facts=facts, section=kind,
            index=i + 1, total=len(kinds), words=per[kind], language=narration_language(),
            written=" | ".join(written) or "(none yet)",
            rank_note=_rank_note(kind, ranks.get(kind), items),
            cta_examples=cta_examples,
        )
        out = generate_parsed("longform", prompt, temperature=0.7).model_dump()
        narration = (out.get("narration") or "").strip()
        if not narration:
            raise RuntimeError(f"long-form section '{kind}' returned no narration")
        headings.append((out.get("heading") or "").strip())
        parts.append(narration)
        written.append(f"{kind}: {narration[:140]}")
        log.info("longform section %d/%d (%s): %d words",
                 i + 1, len(kinds), kind, len(narration.split()))

    # hook = intro, cta = outro, body = the ranked items carrying chapter
    # markers ("## heading") that the long-form storyboard splits on
    hook, cta = parts[0], parts[-1]
    body = "\n\n".join(f"## {h}\n{p}" if h else p
                       for h, p in zip(headings[1:-1], parts[1:-1]))
    words = len(f"{hook} {body} {cta}".split())
    sid = db.insert("scripts", idea_id=idea["id"], version=1, hook=hook,
                    body=body, cta=cta,
                    duration=max(15, round(words / LONGFORM_WPS)),
                    status="DRAFT", created_at=datetime.now().isoformat())
    db.update("ideas", idea["id"], status="SCRIPTED")
    log.info("longform script %d for idea %d: %d items, %d words (~%ds)",
             sid, idea["id"], items, words, round(words / LONGFORM_WPS))
    return sid


def generate_script(db: Database, idea_id: int, project_id: int | None = None) -> int:
    """Generate script, persist, return script id."""
    idea = db.get("ideas", idea_id)
    res = db.all("research", "idea_id=?", (idea_id,))
    facts = json.dumps([f["claim"] for f in json.loads(res[0]["facts"]) if f["status"] == "VERIFIED"]) if res else ""

    target = _target_duration(project_id)
    if target >= LONGFORM_MIN:
        return generate_longform_script(db, idea, facts, target, project_id)

    prompt = render_prompt(
        "scripts", topic=idea["topic"], facts=facts,
        hook=idea.get("hook", ""), language=narration_language(),
    )
    out = generate_parsed("script", prompt, temperature=0.6).model_dump()
    # sync duration to actual narration length. Measured Kokoro pace ≈2.1
    # spoken words/sec (incl. pauses) — 2.5 underestimated by ~19% and let
    # scripts sail past the QC duration cap (project 14: est 67s → actual 80s).
    words = len(out["body"].split())
    out["duration_seconds"] = max(15, round(words / 2.1))
    # hard budget: QC caps a Short's final video at quality.max_duration (75s).
    # Long-form projects raise the word budget via target_duration (see _max_words).
    max_words = _max_words(project_id)
    if words > max_words:
        log.warning("script %d words=%d > %d — retrying shorter", idea_id, words, max_words)
        retry_prompt = prompt + (
            f"\n\nHARD LIMIT: previous draft was too long ({words} words).\n"
            f"Rewrite 'body' with AT MOST {max_words} words total. Keep the "
            f"strongest 2-3 facts; cut detail sentences, not whole facts."
        )
        out2 = generate_parsed("script", retry_prompt, temperature=0.5).model_dump()
        w2 = len(out2["body"].split())
        if abs(w2 - max_words) < abs(words - max_words):   # keep the closer one
            out = out2
            words = w2
            out["duration_seconds"] = max(15, round(words / 2.1))
        log.info("retry: %d words → %ds estimate", words, out["duration_seconds"])
        # HARD ENFORCEMENT: if the LLM still won't obey, truncate on sentence
        # boundaries so the script can NEVER exceed the QC budget again.
        # (project 55: 177 words → 84s → final video 76.5s > 75s → QC FAILED,
        #  and retries were stuck forever because scenes were cached)
        if words > max_words:
            import re as _re
            sents = _re.split(r"(?<=[.!?])\s+", out["body"].strip())
            kept, count = [], 0
            for s in sents:
                sw = len(s.split())
                if count + sw > max_words and kept:
                    break
                kept.append(s)
                count += sw
            out["body"] = " ".join(kept)
            words = len(out["body"].split())
            out["duration_seconds"] = max(15, round(words / 2.1))
            log.warning("script %d hard-truncated to %d words (QC budget)",
                        idea_id, words)
    sid = db.insert("scripts", idea_id=idea_id, version=1, hook=out["hook"],
                    body=out["body"], cta=out["cta"], duration=out["duration_seconds"],
                    status="DRAFT", created_at=datetime.now().isoformat())
    db.update("ideas", idea_id, status="SCRIPTED")
    log.info("script %d generated for idea %d (%ds)", sid, idea_id, out["duration_seconds"])
    return sid