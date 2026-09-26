"""Backfill the channel promo to every video that does not have it yet.

Idempotent: ``apply_promo()`` strips any superseded block and appends the
current copy, returning "skipped" when the description already matches. The
video's own description text is never rewritten — only the promo block is
appended or refreshed.

Quota-aware: ``videos.update`` costs 50 units, so a full pass over ~156 videos
can exhaust the 10,000-unit daily budget. On a quota 403 the run stops cleanly
and prints what is left; re-running after the 07:00 UTC reset picks up the rest,
because every already-done video reports "skipped".

Usage: promo_backfill.py [limit]
"""
import sys
import time

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.uploader.youtube import apply_promo, list_channel_videos

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def main() -> int:
    vids = list_channel_videos()
    todo = vids[:LIMIT] if LIMIT else vids
    print(f"channel: {len(vids)} videos, processing {len(todo)}", flush=True)

    updated = skipped = failed = 0
    for i, v in enumerate(todo):
        try:
            result = apply_promo(v["id"])
        except Exception as exc:
            failed += 1
            msg = str(exc)[:70]
            print(f"  ! {v['id']}: {msg}", flush=True)
            if "403" in msg or "quota" in msg.lower():
                print(f"\nquota exhausted after {i} videos — stopping. "
                      f"{len(todo) - i - 1} left for the next run.", flush=True)
                break
            continue
        if result == "skipped":
            skipped += 1
        else:
            updated += 1
            print(f"  + {v['id']}: {result}: {v['title'][:50]}", flush=True)
        if (i + 1) % 10 == 0:      # gentle pacing — stay under the per-second cap
            time.sleep(1)

    done = updated + skipped
    print(f"\ndone: {updated} updated, {skipped} already had promo, "
          f"{failed} failed — {done}/{len(todo)} carry the promo now", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
