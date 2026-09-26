"""Feedback loop — weighted statistical scoring from historical performance.

Not ML: plain averages per topic keyword, blended with the LLM's base score.
Ideas about topics that performed well get a higher priority score.
"""
from __future__ import annotations

import re

from core.logging import get_logger

log = get_logger("feedback")

# weight of historical performance vs base LLM score (0..1)
HISTORY_WEIGHT = 0.4


def _topic_keywords(topic: str) -> list[str]:
    """Lowercase words >3 chars, minus stopwords — cheap topic signature."""
    stop = {"the", "and", "for", "with", "that", "this", "from", "what",
            "how", "why", "your", "about", "into", "without"}
    words = re.findall(r"[a-z]{4,}", topic.lower())
    return [w for w in words if w not in stop]


def topic_performance(db) -> dict[str, float]:
    """Average normalized views (0..1) per topic keyword across projects."""
    rows = db.all("projects", "views IS NOT NULL")
    per_kw: dict[str, list[float]] = {}
    max_views = max((r["views"] for r in rows), default=0) or 1
    for p in rows:
        idea = db.get("ideas", p.get("idea_id")) if p.get("idea_id") else None
        if not idea:
            continue
        norm = p["views"] / max_views
        for kw in _topic_keywords(idea["topic"]):
            per_kw.setdefault(kw, []).append(norm)
    return {kw: sum(v) / len(v) for kw, v in per_kw.items() if v}


def rescore_pending_ideas(db) -> int:
    """Blend base idea scores with historical topic performance.

    Returns number of ideas updated. Ideas in queue (status='pending')
    get re-ranked so the best-performing topics surface first.
    """
    perf = topic_performance(db)
    if not perf:
        return 0
    updated = 0
    for idea in db.all("ideas", "status=?", ("pending",)):
        kws = _topic_keywords(idea["topic"])
        hits = [perf[kw] for kw in kws if kw in perf]
        hist = sum(hits) / len(hits) if hits else 0.5  # neutral: unknown topic
        base = (idea.get("score") or 50) / 100.0
        blended = (1 - HISTORY_WEIGHT) * base + HISTORY_WEIGHT * hist
        db.update("ideas", idea["id"], score=round(blended * 100, 1))
        updated += 1
    log.info("rescored %d pending ideas (topics tracked: %d)", updated, len(perf))
    return updated
