"""Analytics collector — pull YouTube stats for uploaded videos.

Uses the YouTube Data API v3 `videos` endpoint with stored OAuth creds.
Feeds the feedback loop: what performs well informs future idea selection.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import config
from core.logging import get_logger

log = get_logger("analytics")

STATS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNEL_URL = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"


def _get_token():
    from apps.uploader.youtube import _access_token, _creds
    return _access_token(_creds())


def fetch_stats(video_id: str) -> dict:
    """Return {title, views, likes, comments} for a video (0s if unavailable)."""
    import requests

    token = _get_token()
    r = requests.get(STATS_URL, params={
        "part": "snippet,statistics", "id": video_id,
    }, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return {"title": "", "views": 0, "likes": 0, "comments": 0}
    item = items[0]
    s = item["statistics"]
    title = item.get("snippet", {}).get("title", "")
    return {"title": title,
            "views": int(s.get("viewCount", 0)),
            "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0))}


def list_channel_video_ids() -> list[str]:
    """Return all video IDs from the channel's uploads playlist."""
    import requests

    token = _get_token()
    # Get uploads playlist ID
    r = requests.get(CHANNEL_URL, params={
        "part": "contentDetails", "mine": "true",
    }, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    playlist_id = r.json()["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Paginate all video IDs
    vids = []
    page_token = None
    while True:
        params: dict = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(PLAYLIST_URL, params=params,
                         headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data["items"]:
            vids.append(item["snippet"]["resourceId"]["videoId"])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return vids


def fetch_all_channel_stats() -> list[dict]:
    """Fetch stats for ALL videos on the channel. Returns list of
    {youtube_id, title, views, likes, comments}."""
    import requests

    token = _get_token()
    vids = list_channel_video_ids()
    out = []
    for i in range(0, len(vids), 50):
        batch = vids[i:i + 50]
        r = requests.get(STATS_URL, params={
            "part": "snippet,statistics", "id": ",".join(batch),
        }, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        for item in r.json().get("items", []):
            s = item["statistics"]
            out.append({
                "youtube_id": item["id"],
                "title": item.get("snippet", {}).get("title", ""),
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            })
    return out


def collect_for_project(db, pid: int) -> dict | None:
    """Update analytics for one project with a youtube_id. Returns stats."""
    from datetime import datetime, timezone
    proj = db.get("projects", pid)
    if not proj or not proj.get("youtube_id"):
        return None
    stats = fetch_stats(proj["youtube_id"])
    db.update("projects", pid, views=stats["views"],
              likes=stats["likes"], comments=stats["comments"])
    # Also upsert into analytics table for historical tracking
    now = datetime.now(timezone.utc).isoformat()
    for u in db.all("uploads", "video_id IN (SELECT id FROM videos WHERE script_id=?)",
                    (proj.get("script_id"),)):
        rows = db.all("analytics", "video_id=?", (u["video_id"],))
        if rows:
            db.update("analytics", rows[0]["id"], title=stats.get("title", ""),
                      views=stats["views"], likes=stats["likes"],
                      comments=stats["comments"], collected_at=now)
        else:
            db.insert("analytics", video_id=u["video_id"],
                      title=stats.get("title", ""),
                      views=stats["views"], likes=stats["likes"],
                      comments=stats["comments"], collected_at=now)
    return stats
