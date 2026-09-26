"""Title generator (spec §33) — YouTube titles + description + hashtags + tags."""
from __future__ import annotations

from core.logging import get_logger
from providers.llm import generate_parsed

log = get_logger("title-gen")

_MAX_EXISTING = 200


def _channel_titles(limit: int = _MAX_EXISTING) -> list[str]:
    """Live titles from the uploads playlist. Best-effort: offline, no
    credentials or an exhausted quota must never block a render."""
    try:
        from apps.uploader.youtube import list_channel_videos

        return [v["title"] for v in list_channel_videos(max_results=limit)
                if v.get("title")]
    except Exception as e:
        log.warning("existing-title guard skipped: %s", e)
        return []


def _prompt_block(titles: list[str]) -> str:
    return "\n".join(f"- {t}" for t in titles) if titles else "(none yet)"


def generate_titles(topic: str, hook: str = "",
                    existing_titles: list[str] | None = None) -> dict:
    """LLM title suggestions. Returns
    {titles: [{title, score, why}], description, hashtags, tags, best}.

    `existing_titles` closes the duplicate-title gap: the LLM is shown what the
    channel has already published, and any suggestion still landing too close to
    one of those is dropped. None fetches them, [] skips the lookup.
    """
    from apps.research.idea_engine import _is_duplicate
    from core.prompts import render_prompt

    if existing_titles is None:
        existing_titles = _channel_titles()

    prompt = render_prompt("titles", topic=topic, hook=hook, summary="",
                           existing=_prompt_block(existing_titles))
    out = generate_parsed("titles", prompt).model_dump()

    raw = [t for t in (out.get("youtube_titles") or []) if t]
    kept = [t for t in raw if not _is_duplicate(t, existing_titles)]
    if raw and not kept:
        # Never hand back an empty set — a duplicate title beats no title.
        log.warning("title guard: all %d suggestions collided; keeping LLM order", len(raw))
        kept = raw
    elif len(kept) < len(raw):
        log.info("title guard: dropped %d of %d suggestions that repeated published titles",
                 len(raw) - len(kept), len(raw))

    # model returns plain ranked strings — expose order as rank-score
    titles = [{"title": t, "score": i + 1, "why": ""} for i, t in enumerate(kept)]
    return {
        "titles": titles,
        "description": out.get("description") or "",
        "hashtags": out.get("hashtags") or [],
        "tags": out.get("tags") or [],
        "best": titles[0] if titles else None,
        "short_titles": [s for s in (out.get("short_titles") or []) if s],
    }
