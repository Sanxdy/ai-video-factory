"""Upload projects held at APPROVAL or UPLOADING once the YouTube upload quota resets.

Projects land here when a run is interrupted before the video reaches YouTube:
AVF_AUTO_APPROVE=0 parks finished videos at APPROVAL, and a 429 mid-upload
leaves them at UPLOADING. Nothing else resumes UPLOADING (`avf produce all` is
not in cron), so both states are collected here. Idempotent: projects that
already have a youtube_id are skipped, and a failed upload parks the project
back at APPROVAL so the next cron tick retries.

Everything lives in main() behind a __main__ guard ON PURPOSE. The work used to
run at import time, so merely importing this module — to read HELD_STATES, for
instance — uploaded and published every held project as a side effect. Cron runs
it as a script, so the guard changes nothing about how it is invoked.
"""
import sys

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.orchestrator.pipeline import get_db, run_pipeline
from apps.uploader.youtube import _api, PROMO_MARK
from core.config import config

HELD_STATES = ("APPROVAL", "UPLOADING")


def main() -> int:
    db = get_db()
    held = []
    for p in db.all("projects"):
        if p.get("status") not in HELD_STATES or p.get("youtube_id"):
            continue
        vid = config.root / "content" / "rendered" / f"project-{p['id']:05d}" / "final.mp4"
        if vid.exists():
            held.append(p["id"])

    if not held:
        print("nothing held at APPROVAL — no-op")
        return 0

    print(f"held projects: {held}")
    for pid in held:
        db.update("projects", pid, status="UPLOADING")
        try:
            run_pipeline(pid)
        except Exception as e:
            print(f"#{pid} UPLOAD FAILED: {type(e).__name__}: {str(e)[:200]}")
            db.update("projects", pid, status="APPROVAL")
            continue

        p = db.get("projects", pid) or {}
        # If run_pipeline returned but youtube_id is still empty, the upload
        # failed transiently (e.g. 429 quota) and the orchestrator left it in
        # APPROVAL. Treat it like a failed attempt so the cron retries.
        yt = p.get("youtube_id")
        print(f"#{pid} uploaded: {yt}")
        if not yt:
            db.update("projects", pid, status="APPROVAL")
            continue
        sn = _api("videos", {"part": "snippet", "id": yt})["items"][0]["snippet"]
        desc = sn.get("description", "")
        print(f"#{pid} title: {sn['title']}")
        print(f"#{pid} promo present: {PROMO_MARK in desc}")
        print(f"#{pid} description:\n{desc[:500]}\n" + "-" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
