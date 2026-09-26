#!/usr/bin/env python3
"""Cleanup (spec §3): prune old renders/caches, keep DB intact.

Usage: python scripts/cleanup.py [--days 14] [--dry-run]
Never touches database/factory.db.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["runtime/cache", "runtime/jobs", "runtime/locks"]
PROTECT = {"database/factory.db"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cutoff = time.time() - args.days * 86400
    freed = 0

    for t in TARGETS:
        d = ROOT / t
        if not d.exists():
            continue
        for item in d.iterdir():
            if item.stat().st_mtime > cutoff:
                continue
            size = sum(f.stat().st_size for f in item.rglob("*")
                       if f.is_file()) if item.is_dir() else item.stat().st_size
            rel = str(item.relative_to(ROOT))
            if rel in PROTECT:
                continue
            print(f"{'[dry]' if args.dry_run else '[rm] '} {rel} ({size/1024:.0f} KB)")
            freed += size
            if not args.dry_run:
                shutil.rmtree(item, ignore_errors=True) if item.is_dir() \
                    else item.unlink(missing_ok=True)

    print(f"{'would free' if args.dry_run else 'freed'} {freed/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
