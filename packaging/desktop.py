#!/usr/bin/env python3
"""Desktop shell: run the API server in-process and show it in a native window.

The entry point of a packaged bundle (see build.py). Also runnable from a source
checkout: `python3 packaging/desktop.py`. With no GUI toolkit available — Linux
without webkit2gtk, say — it falls back to the system browser, which is the same
app minus the window frame.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
SOURCE = BUNDLE.parent
if (SOURCE / "core").is_dir():
    # A source checkout: the bundle case needs nothing, the interpreter that runs
    # this file already has its own site-packages (where the app lives) on sys.path.
    sys.path.insert(0, str(SOURCE))

HOST = "127.0.0.1"
TITLE = "AVF Console"

# Shortest time the splash stays on screen after the window is shown. Small
# enough not to delay anyone, long enough to be seen at all.
SPLASH_MIN_SECS = 1.5

# Shown while the API server boots and the first Next.js bundle is parsed — a
# cold start is a few seconds of nothing otherwise. Inline rather than served:
# the window is created before the server can answer, so a served splash could
# itself 404, and this page must render with no network at all.
SPLASH = """<!doctype html><html><head><meta charset="utf-8">
<title>AVF Console</title><style>
  :root { color-scheme: light; }
  html, body { height: 100%; margin: 0; }
  body {
    display: grid; place-content: center; gap: 18px; justify-items: center;
    background: #EFF8F7; color: #10312F;
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-user-select: none; user-select: none;
  }
  .mark { width: 68px; height: 68px; }
  .name { font-size: 17px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; color: #1E8C86; }
  .sub { color: #547370; font-size: 12.5px; }
  .bar { width: 190px; height: 3px; border-radius: 2px; background: #CDE7E4; overflow: hidden; }
  .bar i { display: block; width: 38%; height: 100%; border-radius: 2px;
           background: #2BA8A2; animation: slide 1.15s ease-in-out infinite; }
  @keyframes slide { 0% { transform: translateX(-100%); }
                     100% { transform: translateX(320%); } }
  @media (prefers-reduced-motion: reduce) { .bar i { animation: none; width: 100%; } }
</style></head><body>
  <svg class="mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <rect x="1.5" y="4" width="21" height="16" rx="4.5" fill="#2BA8A2"/>
    <path d="M10 8.5L16 12L10 15.5V8.5Z" fill="#10312F"/>
  </svg>
  <div class="name">AVF Console</div>
  <div class="bar"><i></i></div>
  <div class="sub" id="s">Starting the local server…</div>
  <script>
    // After ~8s say what is actually slow, so a long wait is not read as a hang.
    setTimeout(function () {
      document.getElementById("s").textContent =
        "Still starting. The first run unpacks the TTS model, which takes a minute.";
    }, 8000);
  </script>
</body></html>"""


def _install_icon() -> None:
    """Linux only: put avf.png in the XDG icon theme so `Icon=avf` resolves.

    A .desktop file's Icon key is looked up in the theme by name — a path
    relative to the .desktop file does not work, and build.py cannot bake an
    absolute path because the folder is unzipped wherever the user wants it.
    Runs once: skipped when the file is already there.
    """
    src = BUNDLE / "avf.png"
    if sys.platform != "linux" or not src.is_file():
        return
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    dst = base / "icons" / "hicolor" / "512x512" / "apps" / "avf.png"
    if dst.is_file():
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError:
        pass  # an icon is not worth failing a launch over


def _source_install() -> Path | None:
    """An existing source checkout holding real data, if there is one.

    AVF_MIGRATE_FROM names one outright. Otherwise walk up from the bundle
    looking for a checkout: `core/config.py` is the tell, so a folder that
    merely happens to contain a database/ is never mistaken for an install.
    A distributed bundle finds nothing and migrates nothing, which is right —
    there is nothing of the user's to carry over.
    """
    env = os.environ.get("AVF_MIGRATE_FROM")
    if env:
        p = Path(env).expanduser()
        return p if (p / "database" / "factory.db").is_file() else None
    for parent in list(BUNDLE.parents)[:3]:
        if (parent / "core" / "config.py").is_file() and \
           (parent / "database" / "factory.db").is_file():
            return parent
    return None


def _migrate(dest: Path) -> None:
    """Copy an existing install into a fresh data dir — once, at first launch.

    A packaged app cannot use the checkout's data in place: the bundle's data
    dir is the platform one, and the repo may be moved or deleted afterwards.
    Copying leaves the original untouched, so a bad first run costs nothing.

    Guarded on the destination being empty: this must never overwrite data the
    app has already written.
    """
    if (dest / "database" / "factory.db").exists():
        return
    src = _source_install()
    if not src:
        return
    for name in ("content", "runtime", "database"):
        d = src / name
        if d.is_dir():
            shutil.copytree(d, dest / name, dirs_exist_ok=True)


def _prepare() -> int:
    """Pick a port, keep data out of the bundle, return the port."""
    from core.config import platform_data_dir, data_dir
    # A bundle lives in a read-only or replaceable location (.app, Program Files,
    # an unzipped folder someone deletes), so state never goes next to the code.
    # A source checkout is different: data_dir() already resolves to the repo,
    # and overriding it here would show an empty install to someone whose
    # projects are sitting right there.
    if not (SOURCE / "core").is_dir():
        os.environ.setdefault("AVF_DATA_DIR", str(platform_data_dir()))
    _migrate(data_dir())
    # Lets the API expose POST /api/quit, which would stop a server install's
    # service if it were reachable there.
    os.environ["AVF_DESKTOP"] = "1"
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _serve(port: int) -> None:
    import uvicorn
    from apps.api.server import app
    uvicorn.Server(uvicorn.Config(app, host=HOST, port=port,
                                  log_level="warning")).run()


def _wait_up(port: int, timeout: float = 60) -> bool:
    """Poll /api/health — the endpoint exists for exactly this."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=2):
                return True
        except Exception:
            time.sleep(0.2)
    return False


def _busy(port: int) -> bool:
    """True while a render is in flight, so closing the window is not silent."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/queue", timeout=2) as r:
            q = json.load(r)
        return bool(q.get("running"))
    except Exception:
        return False


def main() -> int:
    _install_icon()
    port = _prepare()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    url = f"http://{HOST}:{port}/"

    try:
        import webview
    except ImportError:
        import webbrowser
        if not _wait_up(port):
            raise SystemExit("server did not come up — see runtime/logs/")
        print(f"{TITLE} → {url}\nCtrl-C to quit.")
        webbrowser.open(url)
        while True:
            time.sleep(3600)

    # The window opens on the splash straight away and swaps to the app once
    # /api/health answers, so a cold start shows progress instead of an empty
    # frame. Waiting first would leave the user with no window for those seconds.
    window = webview.create_window(TITLE, html=SPLASH, width=1440, height=900,
                                   min_size=(1024, 640))
    # pywebview blocks <a download> unless told otherwise, and the UI's entire
    # output path is three of those links: video, subtitles, thumbnail. Links
    # opened with target=_blank already go to the system browser by default,
    # which is what the YouTube OAuth flow needs.
    webview.settings["ALLOW_DOWNLOADS"] = True

    shown: list[float] = []          # when the window actually hit the screen

    def _on_shown() -> None:
        shown.append(time.time())

    window.events.shown += _on_shown

    def _boot() -> None:
        if not _wait_up(port):
            window.load_html(
                "<body style='background:#09090B;color:#E4E4E7;"
                "font:14px system-ui;display:grid;place-content:center;height:100vh;"
                "margin:0;text-align:center'>"
                "<div><b>The server did not start.</b><br><br>"
                "See <code>runtime/logs/desktop.log</code> in the AVF data folder."
                "</div></body>")
            return
        # Hold the splash to a minimum even on a warm start. Measured on the
        # build machine: /api/health answered at 0.41s but the window only
        # painted at 0.53s — load_url fired before the first frame, so the
        # splash never showed and the launch looked like it had none. Waiting
        # for `shown` and flooring the display time turns it into a deliberate
        # beat; a genuinely slow start waits no longer than it already would.
        deadline = time.time() + 10
        while not shown and time.time() < deadline:
            time.sleep(0.05)
        wait = SPLASH_MIN_SECS - (time.time() - (shown[0] if shown else time.time()))
        if wait > 0:
            time.sleep(wait)
        window.load_url(url)

    def _closing() -> bool:
        # Closing mid-render is allowed (the pipeline resumes), but never silent.
        if not _busy(port):
            return True
        return bool(window.evaluate_js(
            "confirm('A video is still rendering.\\n\\n"
            "Close anyway? The job resumes on the next start.')"))

    window.events.closing += _closing
    threading.Thread(target=_boot, daemon=True).start()
    webview.start()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        # pythonw.exe has no console on Windows: an unhandled error would just
        # make the window never appear, with nothing to read.
        try:
            from core.config import data_dir
            log = data_dir() / "runtime" / "logs" / "desktop.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc())
        except Exception:
            traceback.print_exc()
        raise
