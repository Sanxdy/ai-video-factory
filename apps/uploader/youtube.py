"""YouTube uploader — OAuth device flow + resumable upload.

Uses google-api-python-client if installed; otherwise falls back to raw
REST via `requests` so the pipeline still works on lean installs.

OAuth setup (one-time):
  1. Google Cloud Console → enable YouTube Data API v3
  2. Create OAuth Client ID (Desktop app) → download client_secret.json
  3. `avf youtube auth` → opens browser, stores refresh token in
     runtime/youtube_token.json (never commit this).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import data_dir
from core.logging import get_logger

log = get_logger("uploader")

TOKEN_PATH = data_dir() / "runtime" / "youtube_token.json"
CLIENT_SECRET = data_dir() / "runtime" / "client_secret.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/youtube.readonly"]
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REDIRECT = "http://localhost"  # must match a redirect URI registered on the client

# Channel promo, appended to the END of every description (see with_promo).
# The opening lines of a YouTube description are what shows in search results and
# above the "Show more" fold, so the video's own summary owns them and the promo
# sits below.
PROMO = """ZenPilot Pro — run all your accounts from one desktop app.

Every account opens in its own isolated Chromium profile: separate cookies and storage, its own proxy, and its own browser settings. Built-in ad blocker, unlimited profiles and services, one-time purchase.

Download free for macOS, Windows & Linux: https://anzenpilot.gumroad.com/l/zenpilot"""
PROMO_MARK = "anzenpilot.gumroad.com/l/zenpilot"

# Superseded promo blocks, kept verbatim so an already-uploaded description is
# rewritten in place rather than stacking a second ad. _strip_superseded matches
# these exactly, so every wording that could have been published belongs here —
# including both bodies of the 2026-09-24 copy, which differ by one phrase.
LEGACY_PROMOS = (
    # pre-2026-09-23 "anti-detect / 50 computers" copy
    """Need 50 accounts that look like 50 different computers?

ZenPilot Pro is the anti-detect browser built for exactly that. Every profile gets its own fingerprint, cookies, and proxy, all from one desktop app.

See it in action: https://vortex-freq.web.id/
Download for macOS, Windows, or Linux: https://vortex-freq.web.id/#download""",
    # 2026-09-23 copy — vortex-freq link, prepended at the top
    """ZenPilot Pro — run all your accounts from one desktop app.

Every account opens in its own isolated Chromium profile: separate cookies and storage, its own proxy, and its own browser settings. Built-in ad blocker, unlimited profiles and services, one-time purchase.

Download free for macOS, Windows & Linux: vortex-freq.web.id""",
    # 2026-09-24 copy — "Get it on Gumroad" closing line
    """ZenPilot Pro — run all your accounts from one desktop app.

Every account opens in its own isolated Chromium profile: separate cookies and storage, its own proxy, and its own browser settings. Built-in ad blocker, unlimited profiles and services, one-time purchase.

Get it on Gumroad: https://anzenpilot.gumroad.com/l/zenpilot""",
    # 2026-09-24 copy, the body published by the first backfill run
    """ZenPilot Pro — run all your accounts from one desktop app.

Every account opens in its own isolated Chromium profile: separate cookies and storage, its own proxy, and its own browser settings. Built-in ad blocker, unlimited profiles, one-time purchase.

Get it on Gumroad: https://anzenpilot.gumroad.com/l/zenpilot""",
)


def _strip_superseded(text: str) -> str:
    """Remove any known promo block — the current copy included.

    Exact replacement only, never truncation: the 2026-09-23 copy was
    *prepended*, so cutting everything after a promo opener would eat the
    video's own description. LEGACY_PROMOS therefore lists every wording that
    could have been published, including both bodies of the 2026-09-24 copy,
    which differ by one phrase.
    """
    for block in (PROMO, *LEGACY_PROMOS):
        text = text.replace(block, "")
    return text.strip()


def with_promo(description: str) -> str:
    """Append the channel promo at the end of the description, once.

    Idempotent via PROMO_MARK. A superseded block left behind by an earlier run
    is removed first, so re-running rewrites it instead of stacking a second ad.
    """
    text = _strip_superseded(description or "")
    if PROMO_MARK in text:
        return text
    return f"{text}\n\n{PROMO}" if text else PROMO


def refresh_promo(description: str) -> str:
    """Rewrite an already-uploaded description: drop a superseded promo block and
    append the current copy at the end. Idempotent."""
    return with_promo(description or "")


def _creds() -> dict:
    """Return stored credentials {refresh_token, client_id, client_secret}."""
    if not TOKEN_PATH.exists():
        raise RuntimeError(
            "No YouTube credentials. Run `avf youtube auth` first.")
    return json.loads(TOKEN_PATH.read_text())


def _access_token(creds: dict) -> str:
    import requests

    r = requests.post(TOKEN_URL, data={
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def save_secret(text: str) -> None:
    """Validate + store a Desktop-app client_secret.json."""
    data = json.loads(text)
    if "installed" not in data:
        raise ValueError("not a Desktop-app client secret — missing 'installed' key")
    CLIENT_SECRET.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_SECRET.write_text(json.dumps(data, indent=2))


def auth_url() -> str:
    """Google consent URL built from the stored client secret."""
    if not CLIENT_SECRET.exists():
        raise RuntimeError(
            f"Put your OAuth client secret at {CLIENT_SECRET} "
            "(Google Cloud Console → OAuth Client ID → Desktop app).")
    secret = json.loads(CLIENT_SECRET.read_text())["installed"]
    return (f"{AUTH_URL}?client_id={secret['client_id']}"
            f"&redirect_uri={REDIRECT}&response_type=code"
            f"&scope={' '.join(SCOPES)}&access_type=offline&prompt=consent")


def finish_auth(pasted: str) -> None:
    """Exchange a pasted redirect URL (or raw ?code= value) for tokens."""
    import requests
    from urllib.parse import urlparse, parse_qs

    if not CLIENT_SECRET.exists():
        raise RuntimeError("save the client secret first")
    secret = json.loads(CLIENT_SECRET.read_text())["installed"]
    pasted = pasted.strip()
    code = parse_qs(urlparse(pasted).query)["code"][0] if "code=" in pasted else pasted
    r = requests.post(TOKEN_URL, data={
        "client_id": secret["client_id"],
        "client_secret": secret["client_secret"],
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
    }, timeout=30)
    r.raise_for_status()
    tok = r.json()
    if "refresh_token" not in tok:
        raise RuntimeError("no refresh_token in response — revoke the app at "
                           "myaccount.google.com/permissions and retry with prompt=consent")
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({
        "refresh_token": tok["refresh_token"],
        "client_id": secret["client_id"],
        "client_secret": secret["client_secret"],
    }, indent=2))


def oauth_flow() -> None:
    """Interactive CLI wrapper around auth_url + finish_auth."""
    import webbrowser

    url = auth_url()
    print("Open this URL and authorize:\n", url)
    webbrowser.open(url)
    print("\nAfter approving, your browser lands on a localhost URL that "
          "won't load — that's fine. Copy the FULL URL from the address bar.")
    pasted = input("Paste the redirected URL (or just the ?code= value): ").strip()
    finish_auth(pasted)
    print(f"Saved → {TOKEN_PATH}")


def upload_video(video: Path, title: str, description: str = "",
                 tags: list[str] | None = None,
                 privacy: str = "unlisted",
                 publish_at: str = "") -> str:
    """Resumable upload. Returns YouTube video ID.

    publish_at (RFC 3339 UTC) schedules the public premiere: the clip goes up
    as private now and YouTube flips it public at that instant.
    """
    import requests

    creds = _creds()
    token = _access_token(creds)
    video = Path(video)
    size = video.stat().st_size

    status = {"privacyStatus": privacy,
              "selfDeclaredMadeForKids": False}
    if publish_at:
        status.update(privacyStatus="private", publishAt=publish_at)
    meta = {
        "snippet": {
            "title": title[:100],
            "description": with_promo(description)[:5000],
            "tags": tags or [],
            "categoryId": "27",  # Education
        },
        "status": status,
    }

    # init resumable session
    r = requests.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json=meta, timeout=60)
    r.raise_for_status()
    upload_uri = r.headers["Location"]

    # single-shot PUT (fine for <100MB; resumable URI allows retry)
    with video.open("rb") as f:
        r2 = requests.put(
            upload_uri,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Length": str(size),
                     "Content-Type": "video/mp4"},
            data=f, timeout=1800)
    r2.raise_for_status()
    vid = r2.json()["id"]
    log.info("uploaded %s → https://youtube.com/watch?v=%s", video.name, vid)
    return vid


# ── playlists ────────────────────────────────────────────────────────
def _api(path: str, params: dict | None = None, body: dict | None = None,
         method: str | None = None):
    """Small REST wrapper for youtube/v3 calls (playlists, playlistItems, videos).

    `method` is the HTTP verb. Left as None it is inferred the old way — POST
    when a body is given, GET otherwise — so existing read/write callers keep
    working. Pass it explicitly for anything else:

    - method="PUT"    → videos.update (edits a video in place)
    - method="DELETE" → videos.delete

    POST /videos is `videos.insert`, NOT update: it creates a new empty video
    and bills the 100/day upload bucket, so a description edit must use PUT.
    """
    import requests
    import time
    token = _access_token(_creds())
    url = f"https://www.googleapis.com/youtube/v3/{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if method is None:
        method = "POST" if body is not None else "GET"
    if body is not None:
        headers["Content-Type"] = "application/json"

    def _call():
        return requests.request(method, url, params=params or {}, json=body,
                                headers=headers, timeout=60)

    r = _call()
    for attempt in range(3):
        if r.status_code not in (429, 500, 502, 503):
            break
        time.sleep(2 ** attempt)  # 1s, 2s, 4s
        r = _call()
    r.raise_for_status()
    return r.json() if r.text.strip() else {}  # DELETE answers 204 No Content


def list_playlists() -> list[dict]:
    """All playlists on the authenticated channel: [{id, title}]."""
    out, page = [], ""
    while True:
        data = _api("playlists", {"part": "snippet", "mine": "true",
                                  "maxResults": 50, **({"pageToken": page} if page else {})})
        out += [{"id": it["id"], "title": it["snippet"]["title"]}
                for it in data.get("items", [])]
        page = data.get("nextPageToken", "")
        if not page:
            return out


def create_playlist(title: str, privacy: str = "public") -> str:
    """Create a channel playlist; returns its ID. Idempotent by title."""
    for pl in list_playlists():
        if pl["title"].strip().lower() == title.strip().lower():
            return pl["id"]
    data = _api("playlists", {"part": "snippet,status"},
                {"snippet": {"title": title[:150]},
                 "status": {"privacyStatus": privacy}})
    log.info("created playlist %s (%s)", title, data["id"])
    return data["id"]


def add_to_playlist(playlist_id: str, video_id: str) -> None:
    _api("playlistItems", {"part": "snippet"},
         {"snippet": {"playlistId": playlist_id,
                      "resourceId": {"kind": "youtube#video",
                                     "videoId": video_id}}})


def set_thumbnail(video_id: str, image: Path) -> bool:
    """Attach a custom thumbnail to an uploaded video (50 quota units).

    Best-effort by design: a thumbnail is cosmetic, so a failure here must
    never fail an upload that already succeeded. YouTube rejects thumbnails
    on unverified channels, and that rejection is not our problem to fix.
    """
    import requests
    try:
        token = _access_token(_creds())
        r = requests.post(
            THUMB_URL, params={"videoId": video_id},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "image/jpeg"},
            data=Path(image).read_bytes(), timeout=120)
        if r.status_code >= 300:
            log.warning("thumbnail rejected for %s: %s %s", video_id,
                        r.status_code, r.text[:200])
            return False
        log.info("thumbnail set for %s (%d bytes)", video_id,
                 Path(image).stat().st_size)
        return True
    except Exception as e:
        log.warning("thumbnail error for %s: %s", video_id, e)
        return False


def list_channel_videos(max_results: int = 200) -> list[dict]:
    """Fetch all video titles from the authenticated channel.
    Uses the uploads playlist (more reliable than search API).
    Returns [{id, title, publishedAt}] sorted by newest first.
    """
    # Get the uploads playlist ID
    ch_data = _api("channels", {"part": "contentDetails", "mine": "true"})
    items = ch_data.get("items", [])
    if not items:
        log.warning("list_channel_videos: no channel found")
        return []
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Paginate through playlist items
    out, page = [], ""
    while len(out) < max_results:
        params = {"part": "snippet", "playlistId": uploads_id,
                  "maxResults": min(50, max_results - len(out))}
        if page:
            params["pageToken"] = page
        data = _api("playlistItems", params)
        out += [{"id": it["snippet"]["resourceId"]["videoId"],
                 "title": it["snippet"]["title"],
                 "publishedAt": it["snippet"]["publishedAt"]}
                for it in data.get("items", [])]
        page = data.get("nextPageToken", "")
        if not page:
            break
    return out


def apply_promo(video_id: str, dry_run: bool = False) -> str:
    """Bring an already-uploaded video's description up to the current promo:
    drop any superseded block and append the current copy at the end.

    Returns one of: 'updated', 'refreshed', 'skipped', 'dry-run'.
    """
    data = _api("videos", {"part": "snippet", "id": video_id})
    items = data.get("items", [])
    if not items:
        raise RuntimeError(f"video {video_id} not found")
    sn = items[0]["snippet"]
    old = sn.get("description", "")
    new = with_promo(old)
    if new == old:
        return "skipped"
    status = "refreshed" if any(b in old for b in LEGACY_PROMOS) else "updated"
    if dry_run:
        return "dry-run"
    # A snippet PUT replaces the WHOLE snippet, so send every field we fetched
    # back verbatim — listing only title/description/tags/categoryId would
    # silently wipe defaultLanguage and localized on every refresh.
    keep = {k: v for k, v in sn.items() if k != "description"}
    keep["description"] = new[:5000]
    _api("videos", {"part": "snippet"}, {"id": video_id, "snippet": keep},
         method="PUT")
    log.info("promo %s for %s", status, video_id)
    return status
