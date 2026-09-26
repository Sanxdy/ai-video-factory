"""Fill the visible hashtag line and the keyword-tags field on published videos.

Two gaps this closes, both from the same root cause: the 16:9 flow supplies its
own title/description, which bypasses the LLM metadata step — so long-form
shipped with an empty keyword-tags field and no hashtag line, while Shorts got
both. The pipeline is fixed; this backfills what is already on YouTube.

Idempotent: a video that already has keyword tags AND a hashtag line is skipped.
Only missing pieces are added, and the description body is never rewritten — the
promo is stripped, the hashtag line inserted, and the promo re-appended last, so
the "promo always last" rule still holds.

The seed for generation is the LIVE YouTube title, not the DB topic: the title
is what the owner actually published (project 290's DB topic is still Indonesian
while its YouTube title is English), so the tags come out in the right language.

Usage: tags_backfill.py [--dry-run] [--limit N]
"""
import re
import sqlite3
import sys

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.uploader.youtube import _api, _strip_superseded, with_promo  # noqa: E402

DB = "/home/ubuntu/ai-video-factory/database/factory.db"
MAX_TAG_CHARS = 500          # YouTube rejects a longer combined total
MAX_TAGS = 30
HASHTAG_RE = re.compile(r"(?:^|\s)#\w")

DRY = "--dry-run" in sys.argv
LIMIT = 0
if "--limit" in sys.argv:
    LIMIT = int(sys.argv[sys.argv.index("--limit") + 1])


def topics() -> dict[str, str]:
    """youtube_id → local topic, used only as a fallback seed."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return {r["youtube_id"]: (r["topic"] or "")
            for r in con.execute("""
                SELECT p.youtube_id, i.topic FROM projects p
                LEFT JOIN ideas i ON i.id = p.idea_id
                WHERE p.youtube_id IS NOT NULL AND p.youtube_id != ''""")}


def trim_tags(tags: list[str]) -> list[str]:
    """Lowercase, dedupe, and stay inside YouTube's 500-character total."""
    out, used = [], 0
    for t in tags:
        t = str(t).strip().lstrip("#").lower()
        if not t or t in out:
            continue
        if used + len(t) + 1 > MAX_TAG_CHARS or len(out) >= MAX_TAGS:
            break
        out.append(t)
        used += len(t) + 1
    return out


def hashtag_line(hashtags: list[str]) -> str:
    seen, out = set(), []
    for h in hashtags:
        h = str(h).strip().lstrip("#")
        if h and h.lower() not in seen:
            seen.add(h.lower())
            out.append(f"#{h}")
    return " ".join(out)


def main() -> int:
    from apps.research.title_gen import generate_titles
    from apps.uploader.youtube import list_channel_videos

    local = topics()
    try:
        videos = list_channel_videos()
    except Exception as e:
        # The very first call is a channels.list — with an exhausted quota the
        # script dies here, before the per-video guard. Exit 0 with a clear
        # message so cron logs a retry note instead of a traceback.
        print(f"cannot enumerate the channel (quota?): {str(e)[:100]}", flush=True)
        print("nothing changed — will retry after the 07:00 UTC reset.", flush=True)
        return 0
    if LIMIT:
        videos = videos[:LIMIT]
    print(f"channel: {len(videos)} videos", flush=True)

    done = skipped = failed = 0
    for i, v in enumerate(videos):
        vid, title = v["id"], v.get("title") or ""
        try:
            sn = _api("videos", {"part": "snippet", "id": vid})["items"][0]["snippet"]
        except Exception as e:
            failed += 1
            msg = str(e)[:70]
            print(f"  ! {vid}: {msg}", flush=True)
            if "403" in msg or "quota" in msg.lower():
                print(f"\nquota exhausted after {i} videos — stopping. "
                      f"{len(videos) - i - 1} left for the next run.", flush=True)
                break
            continue

        desc = sn.get("description", "") or ""
        body = _strip_superseded(desc)          # drop the promo, keep the body
        have_tags = bool(sn.get("tags"))
        have_hash = bool(HASHTAG_RE.search(body))

        if have_tags and have_hash:
            skipped += 1
            print(f"  = {vid}: already complete", flush=True)
            continue

        seed = title or local.get(vid) or ""
        if not seed:
            failed += 1
            print(f"  ! {vid}: no seed text", flush=True)
            continue
        try:
            meta = generate_titles(seed, "", existing_titles=[])
        except Exception as e:
            failed += 1
            print(f"  ! {vid}: LLM failed: {str(e)[:60]}", flush=True)
            continue

        changes = []
        new_desc = body
        if not have_hash:
            line = hashtag_line(meta.get("hashtags") or [])
            if line:
                new_desc = f"{new_desc}\n\n{line}" if new_desc else line
                changes.append(f"hashtags: {line}")
        new_tags = sn.get("tags") or []
        if not have_tags:
            new_tags = trim_tags(meta.get("tags") or [])
            changes.append(f"tags: {new_tags}")

        if not changes:
            failed += 1
            print(f"  ! {vid}: LLM returned nothing usable", flush=True)
            continue
        if DRY:
            print(f"  ~ {vid}: would set {'; '.join(changes)}", flush=True)
            done += 1
            continue

        snippet = {k: val for k, val in sn.items() if k not in ("description", "tags")}
        snippet["description"] = with_promo(new_desc)[:5000]   # promo goes last
        if new_tags:
            snippet["tags"] = new_tags
        try:
            _api("videos", {"part": "snippet"},
                 {"id": vid, "snippet": snippet}, method="PUT")
        except Exception as e:
            failed += 1
            print(f"  ! {vid}: PUT failed: {str(e)[:70]}", flush=True)
            continue
        done += 1
        print(f"  + {vid}: {'; '.join(changes)}", flush=True)

    print(f"\ndone: {done} updated, {skipped} already complete, {failed} failed",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
