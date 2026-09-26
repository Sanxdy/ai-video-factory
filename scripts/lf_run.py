"""Resume the long-form countdown project from GENERATING_ASSETS to APPROVAL.

Run in the background: assets + audio + subtitles + a ~35 minute render on a
2-core VPS. Stops at APPROVAL — nothing is published by this script.

Behind a __main__ guard so importing it (to read a helper) cannot start a
render as a side effect.
"""
import sys

sys.path.insert(0, "/home/ubuntu/ai-video-factory")

from apps.orchestrator.pipeline import get_db, run_pipeline


def main() -> int:
    pid = int(sys.argv[1])
    db = get_db()
    p = db.get("projects", pid)
    print(f"RESUME {pid}: status={p['status']} script={p['script_id']} "
          f"ratio={p['aspect_ratio']}", flush=True)
    end = run_pipeline(pid)
    p = db.get("projects", pid)
    print(f"END {pid}: state={end} status={p['status']} youtube_id={p['youtube_id']}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
