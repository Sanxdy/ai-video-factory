"""Research agent (spec §9) — factual summary, UNVERIFIED handling.

In $0-local mode we do NOT hit paid search APIs: the LLM produces the
research JSON with explicit VERIFIED/UNVERIFIED status. Source URLs are
remembered as given (model knowledge). Confidence is model-reported.
"""
from __future__ import annotations

import json
from datetime import datetime

from core.database import Database
from core.prompts import render_prompt
from providers.llm import generate_parsed
from providers.tts.kokoro import narration_language
from core.logging import get_logger

log = get_logger("research")

# A "source" that is really an admission of no source. Anything here, or blank,
# cannot support a VERIFIED claim.
_NO_SOURCE = {"", "-", "n/a", "na", "none", "unknown", "various sources",
              "model knowledge", "general knowledge", "common knowledge"}


def _enforce_named_sources(facts: list[dict]) -> list[dict]:
    """A VERIFIED claim without a named source is not verified.

    The prompt asks for a named source; this makes it binding, so a model that
    answers from memory alone cannot get a claim into the script — only
    VERIFIED facts are used downstream.
    """
    out = []
    for f in facts:
        f = dict(f)
        src = (f.get("source") or "").strip()
        if f.get("status") == "VERIFIED" and src.lower() in _NO_SOURCE:
            log.info("research: demoted a VERIFIED claim with no named source")
            f["status"] = "UNVERIFIED"
            f["source"] = ""
        out.append(f)
    return out


def research_idea(db: Database, idea_id: int) -> dict:
    idea = db.get("ideas", idea_id)
    if not idea:
        raise ValueError(f"idea {idea_id} not found")
    prompt = render_prompt("research", topic=idea["topic"],
                        language=narration_language())
    out = generate_parsed("research", prompt).model_dump()
    out["facts"] = _enforce_named_sources(out["facts"])
    db.insert("research", idea_id=idea_id, summary=out["summary"],
              facts=json.dumps(out["facts"]), sources=json.dumps(out["sources"]),
              confidence=out["confidence"], created_at=datetime.now().isoformat())
    db.update("ideas", idea_id, status="RESEARCHED")
    log.info("researched idea %s: conf=%.2f facts=%d", idea_id,
             out["confidence"], len(out["facts"]))
    return out