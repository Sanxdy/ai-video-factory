"""Idea engine (spec 8) - generate + score content ideas via LLM."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from core.config import config
from core.logging import get_logger
from core.prompts import render_prompt
from providers.llm import generate_parsed

log = get_logger("idea-engine")


def _normalize(topic: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", topic.lower()).split()


def _topic_keywords(topic: str) -> set[str]:
    STOP = {"a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "up", "about", "into", "over", "after",
            "is", "are", "was", "were", "be", "been", "being", "have", "has",
            "had", "do", "does", "did", "will", "would", "shall", "should",
            "may", "might", "must", "can", "could", "this", "that", "it",
            "its", "your", "my", "our", "their", "his", "her", "what", "how",
            "why", "when", "where", "who", "which", "than", "also", "just",
            "only", "very", "too", "more", "most", "some", "each", "every"}
    return {w for w in _normalize(topic) if len(w) >= 3 and w not in STOP}


def _is_duplicate(new_topic: str, existing_topics: list[str], threshold: float = 0.65) -> bool:
    new_kw = _topic_keywords(new_topic)
    new_norm = " ".join(_normalize(new_topic))
    for existing in existing_topics:
        exist_kw = _topic_keywords(existing)
        exist_norm = " ".join(_normalize(existing))
        if new_kw and exist_kw:
            overlap = len(new_kw & exist_kw) / min(len(new_kw), len(exist_kw))
            if overlap > 0.7:
                log.info("duplicate guard: keyword overlap %.0f%% - %s vs %s",
                         overlap * 100, new_topic[:40], existing[:40])
                return True
        ratio = SequenceMatcher(None, new_norm, exist_norm).ratio()
        if ratio > threshold:
            log.info("duplicate guard: similarity %.0f%% - %s vs %s",
                     ratio * 100, new_topic[:40], existing[:40])
            return True
    return False


def _get_existing_topics(db, include_new_ideas: bool = False) -> list[str]:
    topics = []
    # From ideas table
    where = "topic IS NOT NULL AND topic != ''" if include_new_ideas else "status != 'NEW' AND topic IS NOT NULL AND topic != ''"
    for row in db.all("ideas", where):
        topics.append(row["topic"])
    # From projects table (via ideas)
    for row in db.all("projects", "idea_id IS NOT NULL"):
        idea = db.get("ideas", row["idea_id"])
        if idea and idea.get("topic"):
            topics.append(idea["topic"])
    # From YouTube channel (live check)
    try:
        from apps.uploader.youtube import list_channel_videos
        videos = list_channel_videos(max_results=200)
        for v in videos:
            if v.get("title"):
                topics.append(v["title"])
        log.info("duplicate guard: fetched %d YouTube channel titles", len(videos))
    except Exception as e:
        log.warning("duplicate guard: could not fetch YouTube titles: %s", e)
    return topics


def generate_ideas(db, count: int = 5, channel: str = "science_shorts", channel_id=None):
    ch = config.channels_cfg.get("channels", {}).get(channel, {})
    prompt = render_prompt(
        "ideas", channel=channel, niche=ch.get("niche", "science"),
        language=ch.get("language", "en"),
    )
    parsed = generate_parsed("idea", prompt, temperature=0.8)
    raw_ideas = parsed.ideas
    existing = _get_existing_topics(db, include_new_ideas=True)
    ideas = []
    for idea in raw_ideas:
        if not idea.topic or not idea.topic.strip():
            continue
        if _is_duplicate(idea.topic, existing):
            log.info("skipping duplicate idea: %s", idea.topic[:60])
            continue
        ideas.append(idea)
        existing.append(idea.topic)
        if len(ideas) >= count:
            break
    if len(ideas) < count:
        log.warning("only %d/%d unique ideas generated (rest were duplicates)", len(ideas), count)
    for idea in ideas:
        db.insert("ideas", topic=idea.topic, source="llm", score=idea.estimated_interest,
                  channel_id=channel_id, status="NEW",
                  created_at=__import__("datetime").datetime.now().isoformat())
    log.info("generated %d unique ideas", len(ideas))
    return ideas


def select_best_idea(db, count: int = 1) -> list[dict]:
    """Select best ideas with diversity weighting & duplicate guard."""
    existing = _get_existing_topics(db, include_new_ideas=False)

    # Get recent project prefixes to prevent format repetition
    recent_proj_rows = db.all("projects", "idea_id IS NOT NULL ORDER BY id DESC LIMIT 5")
    recent_prefixes = []
    for p in recent_proj_rows:
        idea = db.get("ideas", p["idea_id"])
        if idea and idea.get("topic"):
            w = idea["topic"].strip().split()[0].lower()
            recent_prefixes.append(w)

    why_count = recent_prefixes.count("why")

    candidates = db.all("ideas", "status='NEW' ORDER BY score DESC")
    scored_candidates = []
    for idea in candidates:
        topic = idea.get("topic")
        if not topic:
            continue
        if _is_duplicate(topic, existing):
            log.info("select_best_idea: skipping duplicate '%s'", topic[:50])
            continue

        first_word = topic.strip().split()[0].lower()
        # Apply diversity adjustment if 'why' is dominant in recent projects
        diversity_penalty = 30.0 if (first_word == "why" and why_count >= 2) else 0.0
        eff_score = float(idea.get("score") or 0.0) - diversity_penalty
        scored_candidates.append((eff_score, idea))

    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    selected = []
    for _, idea in scored_candidates:
        selected.append(idea)
        existing.append(idea["topic"])
        if len(selected) >= count:
            break
    return selected
