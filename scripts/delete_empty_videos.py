"""Delete the 0-duration videos that the promo-backfill insert bug created.

`_api()` used to POST for writes, and POST /youtube/v3/videos is
`videos.insert`, so every `apply_promo()` "description update" created a new
empty (0-duration), PUBLIC video instead of editing the target. Three backfill
runs (2026-09-20, 09-22, 09-23) left 113 of them on the channel and exhausted
the 100/day `videos.insert` bucket, which then 429'd the real daily uploads.

This is destructive and irreversible, so it is opt-in:

    .venv/bin/python scripts/delete_empty_videos.py --dry-run
    .venv/bin/python scripts/delete_empty_videos.py --limit 60

A 0-second video is never a real upload, so `duration == "P0D"` is the whole
predicate. `videos.delete` costs 50 units of the general 10,000/day pool, so
113 deletes is 5,650 units -- pause the promo backfill for that day or split it
across two.
"""
import argparse
import sys

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.uploader.youtube import _api, list_channel_videos

EMPTY = ("P0D", "PT0S")


def find_empty() -> tuple[list[dict], list[str]]:
    """Return (junk, refusals).

    A junk video must satisfy BOTH signals: 0-second duration AND no
    fileDetails. fileDetails is only populated for videos the channel actually
    owns and processed, so a real upload can never be 0s with no file. Any id
    that also appears as a tracked project `youtube_id` is refused outright.
    """
    import sqlite3

    from core.config import config

    db = sqlite3.connect(str(config.root / "database" / "factory.db"))
    tracked = {r[0] for r in db.execute(
        "SELECT youtube_id FROM projects WHERE youtube_id IS NOT NULL AND youtube_id != ''")}

    vids = list_channel_videos(max_results=1000)
    junk, refusals = [], []
    for i in range(0, len(vids), 50):
        chunk = vids[i:i + 50]
        data = _api("videos", {"part": "contentDetails,snippet,fileDetails",
                               "id": ",".join(v["id"] for v in chunk)})
        for it in data.get("items", []):
            vid = it["id"]
            zero = it["contentDetails"]["duration"] in EMPTY
            no_file = not (it.get("fileDetails") or {}).get("fileSize")
            if not (zero and no_file):
                continue
            if vid in tracked:
                refusals.append(vid)  # safety net; must never trigger
                continue
            junk.append({"id": vid,
                         "publishedAt": it["snippet"]["publishedAt"],
                         "title": it["snippet"]["title"]})
    junk.sort(key=lambda x: x["publishedAt"])
    return junk, refusals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be deleted, delete nothing")
    ap.add_argument("--limit", type=int, default=0,
                    help="delete at most N (0 = all); use ~60 per day to stay "
                         "inside the 10,000-unit quota alongside the backfill")
    args = ap.parse_args()

    empty, refusals = find_empty()
    print(f"found {len(empty)} zero-duration videos with no file")
    if refusals:
        print(f"REFUSED {len(refusals)} id(s) that are tracked project videos: {refusals}")
    for v in empty:
        print(f"  {v['publishedAt']}  {v['id']}  {v['title'][:60]}")
    if args.dry_run:
        print("(dry-run: nothing deleted)")
        return

    todo = empty[:args.limit] if args.limit else empty
    print(f"deleting {len(todo)} ...")
    failed = 0
    for v in todo:
        try:
            _api("videos", {"id": v["id"]}, method="DELETE")
            print(f"  deleted {v['id']}")
        except Exception as e:
            failed += 1
            print(f"  FAILED {v['id']}: {str(e)[:160]}")
    print(f"done: {len(todo) - failed} deleted, {failed} failed, "
          f"{len(empty) - len(todo)} left for a later run")


if __name__ == "__main__":
    main()
