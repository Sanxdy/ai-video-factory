"""Stock footage — real motion clips per scene (BYOK, free tiers).

Sources: Pexels (settings key image.pexels_key) and Pixabay (settings key
image.pixabay_key). Which of them get searched comes from settings key
video.stock_sources — default "pexels", so anything that never touched this
setting behaves exactly as before.

Mode switch: settings key video.asset_mode = auto | image | stock.
auto = stock when at least one configured source has a key, else AI images.

Pixabay's API terms (pixabay.com/api/docs) shape the implementation:
  * "requests must be cached for 24 hours" — every search response is cached on
    disk for 24h and reused, so a repeated query costs zero further requests.
  * "do not send lots of automated queries" / "Systematic mass downloads are
    not allowed" — Pixabay is asked only the scene's PRIMARY query, never the
    shortened fallback chain Pexels gets, and calls are throttled far below
    their 100 req/60s ceiling.
  * "please download them to your server first" — we always download to disk;
    a Pixabay URL is never hotlinked in a finished video.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections import deque
from pathlib import Path

import httpx

from core.config import data_dir
from core.errors import ProviderError
from core.filesystem import cache_key
from core.settings import get_setting

PROVIDERS = ("pexels", "pixabay")

# Pixabay allows 100 requests per 60s. Stay far below it: a production pipeline
# must not look like a scraper and we never need the headroom.
_PIXABAY_MAX_PER_MIN = 30
_PIXABAY_HITS: deque[float] = deque()
# Widest page Pixabay offers — one request can serve many scenes.
_PIXABAY_PER_PAGE = 100

_SEARCH_TTL = 24 * 3600  # Pixabay: "requests must be cached for 24 hours"

# Words that describe our own frame furniture, never a subject. A prompt made
# only of these yields a query that can never match anything (measured: 43 of
# 67 scenes in project 290 produced "framing text watermarks subtitles").
_JUNK = {"framing", "text", "watermark", "watermarks", "subtitle", "subtitles",
         "horizontal", "vertical", "overlay", "caption", "captions",
         "1920x1080", "1080x1920", "3840x2160", "2160x3840"}
_RESOLUTION = re.compile(r"^\d{3,4}x\d{3,4}$")


def _is_junk_query(query: str) -> bool:
    """True when every word is frame furniture or a resolution, so the query
    cannot possibly describe a subject."""
    words = query.lower().split()
    return bool(words) and all(w in _JUNK or _RESOLUTION.match(w) for w in words)

_SEARCH = {}  # filled at the bottom: provider name -> search function


def extract_stock_query_from_prompt(visual_prompt: str) -> str:
    """Extract a short search query from a verbose visual_prompt.

    When the LLM fails to provide stock_query, this extracts the core subject
    from the visual_prompt by stripping cinematic framing words and keeping
    only concrete nouns. Returns "" when nothing searchable is left, so the
    caller skips the query instead of burning a request on frame furniture.
    """
    if not visual_prompt:
        return ""
    # Strip the cinematic prefix if present
    base = visual_prompt
    if ":" in base:
        base = base.split(":", 1)[1]
    # Remove common framing/filler words
    STOP = {"vertical", "1080x1920", "shot", "a", "an", "the", "of", "on",
            "with", "at", "from", "in", "its", "own", "cinematic", "style",
            "close-up", "wide", "macro", "split-screen", "extreme", "detailed",
            "vibrant", "dimly", "lit", "weather", "balanced", "composition",
            "mysterious", "dramatic", "showing", "featuring", "against",
            "beautiful", "stunning", "gorgeous", "epic", "amazing"}
    # Strip punctuation from each word
    import string
    words = [w.strip(string.punctuation) for w in base.split()]
    kept = [w for w in words if w.lower() not in STOP and len(w) > 2]
    # Take first 3-4 meaningful words
    query = " ".join(kept[:4])
    if not query or _is_junk_query(query):
        return ""
    return query[:60]


def pexels_key() -> str:
    return get_setting("image.pexels_key") or ""


def pixabay_key() -> str:
    return get_setting("image.pixabay_key") or ""


def stock_sources() -> list[str]:
    """Configured sources that actually have a key, in priority order."""
    raw = get_setting("video.stock_sources") or "pexels"
    want = [s.strip().lower() for s in raw.split(",") if s.strip()]
    keys = {"pexels": pexels_key(), "pixabay": pixabay_key()}
    out = [s for s in want if s in PROVIDERS and keys[s]]
    if not out and keys["pexels"]:
        out = ["pexels"]  # never lose stock over a typo in the setting
    return out


def stock_enabled() -> bool:
    mode = get_setting("video.asset_mode") or "auto"
    if mode == "image":
        return False
    return bool(stock_sources())


# ── search cache (Pixabay requires 24h; it also dedupes within a run) ──

def _cache_dir() -> Path:
    d = data_dir() / "runtime" / "cache" / "stock-search"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file(provider: str, query: str, min_w: int, orientation: str) -> Path:
    return _cache_dir() / f"{cache_key(provider, query, str(min_w), orientation)}.json"


def _cached(provider: str, query: str, min_w: int, orientation: str) -> list[dict] | None:
    p = _cache_file(provider, query, min_w, orientation)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < _SEARCH_TTL:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    return None


def _store_cache(provider: str, query: str, min_w: int, orientation: str,
                 rows: list[dict]) -> None:
    try:
        _cache_file(provider, query, min_w, orientation).write_text(json.dumps(rows))
    except OSError:
        pass


# ── per-provider search → uniform candidates (no download yet) ──

def _fits(f: dict, orientation: str) -> bool:
    w, h = f.get("width") or 0, f.get("height") or 0
    return w >= h if orientation == "landscape" else h >= w


def _pexels_search(query: str, min_w: int, orientation: str) -> list[dict]:
    key = pexels_key()
    if not key:
        raise ProviderError("no Pexels API key configured")
    rows = _cached("pexels", query, min_w, orientation)
    if rows is not None:
        return rows
    r = httpx.get("https://api.pexels.com/videos/search",
                  params={"query": query, "per_page": 15,
                          "orientation": orientation},
                  headers={"Authorization": key}, timeout=30)
    r.raise_for_status()
    rows = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4"
                 and (f.get("width") or 0) >= min_w and _fits(f, orientation)]
        if not files:
            continue
        files.sort(key=lambda f: f["width"])  # smallest sufficient = fast download
        rows.append({"provider": "pexels", "id": v.get("id"),
                     "url": files[0]["link"],
                     "haystack": (str(v.get("url", "")) + " "
                                  + str(v.get("title", ""))).lower()})
    _store_cache("pexels", query, min_w, orientation, rows)
    return rows


def _pixabay_throttle() -> None:
    """Keep Pixabay under _PIXABAY_MAX_PER_MIN requests per rolling minute."""
    while True:
        now = time.monotonic()
        while _PIXABAY_HITS and now - _PIXABAY_HITS[0] > 60:
            _PIXABAY_HITS.popleft()
        if len(_PIXABAY_HITS) < _PIXABAY_MAX_PER_MIN:
            _PIXABAY_HITS.append(time.monotonic())
            return
        time.sleep(60 - (now - _PIXABAY_HITS[0]) + 0.05)


def _pixabay_search(query: str, min_w: int, orientation: str) -> list[dict]:
    key = pixabay_key()
    if not key:
        raise ProviderError("no Pixabay API key configured")
    rows = _cached("pixabay", query, min_w, orientation)
    if rows is not None:
        return rows
    _pixabay_throttle()
    params = {"key": key, "q": query, "per_page": _PIXABAY_PER_PAGE,
              "safesearch": "true"}
    if orientation == "landscape":
        params["min_width"] = min_w
    else:
        params["min_height"] = min_w
    r = httpx.get("https://pixabay.com/api/videos/", params=params, timeout=30)
    r.raise_for_status()
    rows = []
    for v in r.json().get("hits", []):
        files = [s for s in (v.get("videos") or {}).values()
                 if s.get("url") and (s.get("width") or 0) >= min_w
                 and _fits(s, orientation)]
        if not files:
            continue
        files.sort(key=lambda f: f["width"])
        rows.append({"provider": "pixabay", "id": v.get("id"),
                     "url": files[0]["url"],
                     "haystack": (str(v.get("tags", "")) + " "
                                  + str(v.get("pageURL", ""))).lower()})
    _store_cache("pixabay", query, min_w, orientation, rows)
    return rows


_SEARCH.update(pexels=_pexels_search, pixabay=_pixabay_search)


def _excluded(cand: dict, exclude_ids: set) -> bool:
    """A project must never reuse a clip. Keys are "provider:id" so two
    providers' numeric ids cannot collide; bare ids are still honoured for
    sidecars and callers written before multi-source existed."""
    return (cand.get("id") in exclude_ids
            or f"{cand['provider']}:{cand.get('id')}" in exclude_ids)


def _download(cand: dict, out_path: Path) -> Path:
    dl = httpx.get(cand["url"], timeout=300, follow_redirects=True)
    dl.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(dl.content)
    out_path.with_suffix(f".{cand['provider']}-id").write_text(str(cand.get("id", "")))
    return out_path


def fetch_scene_stock(scene: dict, out_path: Path,
                      exclude_ids: set | None = None,
                      orientation: str = "portrait") -> Path:
    """Fetch a clip for one scene, trying narration then visual prompt.

    Narration is genuinely different per scene while visual prompts are often
    repeated, so narration makes the better first query. Falling back to the
    visual prompt rescues scenes whose narration has no searchable subject
    (project 246: 2 of 6 scenes missed on narration alone).

    `orientation` must match the project frame: a portrait clip cropped to a
    16:9 frame is a narrow, blurry centre strip, so landscape projects must
    ask for landscape footage or they are better off with stills.
    """
    candidates: list[str] = []
    for text in (scene.get("stock_query"), scene.get("narration"),
                 scene.get("visual_prompt")):
        q = extract_stock_query_from_prompt(text or "")
        if q and q not in candidates:
            candidates.append(q)
    for q in candidates:
        try:
            return fetch_stock_video(q, out_path, exclude_ids=exclude_ids,
                                     orientation=orientation)
        except (ProviderError, httpx.HTTPError):
            continue
    raise ProviderError(f"no stock video for scene {scene.get('scene_number')}")


def fetch_stock_video(query: str, out_path: Path, min_w: int | None = None,
                      exclude_ids: set | None = None,
                      orientation: str = "portrait") -> Path:
    """Download the best clip for query across every configured source.

    `query` is the scene's stock_query when the storyboard provided one
    (concrete subject, e.g. "camel"). The providers match best on ONE primary
    keyword, so we try the full query first, then progressively shorter
    variants ("lion savanna" → "lion") — but only on Pexels. Pixabay sees the
    primary query alone, which is what keeps our request volume defensible
    under their terms.

    A result is accepted only when the provider's own text (title/url for
    Pexels, tags/pageURL for Pixabay) actually mentions a query word —
    otherwise we raise so the pipeline falls back to the accurate AI-image
    path instead of shipping unrelated footage.

    `exclude_ids` holds clips already used by this project: a project must
    never repeat the same clip across scenes (project 246 shipped six
    identical scenes because every scene resolved to the same top hit).
    """
    sources = stock_sources()
    if not sources:
        raise ProviderError("no stock source configured")
    if min_w is None:
        min_w = 1280 if orientation == "landscape" else 720
    exclude_ids = exclude_ids or set()
    words = [w for w in query.lower().split() if len(w) > 2]
    # longest → shortest: "lion savanna" tries "lion savanna", then "lion".
    # Short generic words are dropped automatically.
    attempts = [" ".join(words[:i + 1]) for i in range(len(words))] or [query]
    attempts = list(dict.fromkeys(reversed(attempts)))
    terms = [w.lower() for w in query.split() if len(w) > 2]
    for i, attempt in enumerate(attempts):
        pool: list[tuple[int, dict]] = []
        for provider in sources:
            # Pixabay TOS: one query per scene, not the whole shortening chain
            if provider == "pixabay" and i > 0:
                continue
            try:
                rows = _SEARCH[provider](attempt, min_w, orientation)
            except (ProviderError, httpx.HTTPError):
                continue
            for cand in rows:
                if _excluded(cand, exclude_ids):
                    continue
                # relevance guard: both providers fuzzy-match anything
                # ("kangaroo rat" → hamsters). Score by how many query words
                # the provider's own text mentions, and drop the zeroes so we
                # never download footage that is merely "something".
                score = sum(1 for t in terms if t in cand["haystack"])
                if terms and score == 0:
                    continue
                pool.append((score, cand))
        if pool:
            # Both providers are searched; the best match wins. Ties are broken
            # at random so successive videos do not keep landing on the same
            # provider's top hit — variety is the whole point of a second index.
            best = max(s for s, _ in pool)
            return _download(random.choice([c for s, c in pool if s == best]),
                             out_path)
    raise ProviderError(f"no relevant stock video for '{query}'")
