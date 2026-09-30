#!/usr/bin/env python3
"""Assemble a runnable AVF bundle for macOS / Windows / Linux — from any of them.

Nothing is frozen on the host: the interpreter is a prebuilt
python-build-standalone tarball for the *target*, and uv resolves every wheel for
the target platform. PyInstaller cannot do this — it bundles the host interpreter
and its .pyd/.dylib files, so it only ever produces a binary for the machine that
runs it.

Layout. `core.config.ROOT` is derived from the location of core/config.py, so the
app dir *is* the interpreter's site-packages, and every resource the app resolves
relative to ROOT has to live there:

    <content>/python/               interpreter (python3 / pythonw.exe)
    .../site-packages/              apps/ core/ providers/ (the wheel)
    .../site-packages/config/       app.yaml, models.yaml, ...
    .../site-packages/prompts/      prompt templates
    .../site-packages/vendor/       ffmpeg, ffprobe, fonts/
    .../site-packages/web/out/      built Next.js UI
    <content>/desktop.py            launcher (pywebview)
    <content>/smoke.py              render check that needs no API key

`<content>` is the stage root everywhere but macOS, where it is
`AVF.app/Contents/Resources` — Finder needs a bundle to launch a process with a
dock icon and no Terminal window. Only the entry point differs per OS:

    macOS     AVF.app/Contents/MacOS/AVF
    Windows   AVF.bat
    Linux     avf.sh + avf.desktop

Usage:
    python3 packaging/build.py macos-arm64
    python3 packaging/build.py all
"""
from __future__ import annotations

import argparse
import hashlib
import re
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "packaging" / "out"
CACHE = OUT / "cache"

PBS_TAG = "20260901"
PBS_BASE = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
            + PBS_TAG)
PBS_PY = "3.11.16"

MR = "https://ffmpeg.martin-riedl.de/download"
# martin-riedl.de answers 403 to urllib's default agent and to an empty one; any
# other string is served. Kept explicit so the fetch does not depend on the host's
# blocklist staying where it is.
UA = "avf-build/1.0 (+https://github.com/Sanxdy/vortex-video-generator)"
DEJAVU = ("https://github.com/dejavu-fonts/dejavu-fonts/releases/download/"
          "version_2_37/dejavu-fonts-ttf-2.37.zip")

# Every download is pinned. The ffmpeg and font hashes come from the publisher's
# own checksum files; python-build-standalone publishes none, so those three were
# recorded from the download itself — the pin still catches a swapped asset.
TARGETS: dict[str, dict] = {
    "macos-arm64": {
        "pbs": "aarch64-apple-darwin",
        "pbs_sha": "768f05cf200273bbdda9a5955a5a6892a4b22f2a0b1e4b0a9160f5c7fce86816",
        "uv": "aarch64-apple-darwin",
        "env": {"MACOSX_DEPLOYMENT_TARGET": "14.0"},
        "site": "python/lib/python3.11/site-packages",
        "python": "python/bin/python3",
        "ffmpeg": [
            (f"{MR}/macos/arm64/1789931890_9.0.2/ffmpeg.zip",
             "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924"),
            (f"{MR}/macos/arm64/1789931890_9.0.2/ffprobe.zip",
             "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6"),
        ],
        "forbid": (".pyd",),      # another OS's native modules must not leak in
        "native": ".so",          # ...and its own must be there
        "app": "AVF.app",         # Finder needs a bundle: dock icon, no Terminal
        "launcher": "AVF",
        "desktop": "webview",     # the shell must actually import on this target
    },
    "windows-x64": {
        "pbs": "x86_64-pc-windows-msvc",
        "pbs_sha": "06cbe479e039f5b9cb5640c286d790074d63f549f92a32d599a3748293bd4510",
        "uv": "windows",
        "env": {},
        "site": "python/Lib/site-packages",
        "python": "python\\pythonw.exe",  # no console window; see LAUNCHERS
        "ffmpeg": [
            # the GitHub release asset of the same build gyan.dev's "release"
            # alias pointed at when the SHA was pinned (byte-identical: 60f4…
            # matches); gyan.dev itself serves it at dial-up speed
            ("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
             "ffmpeg-9.0.2-essentials_build.zip",
             "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"),
        ],
        "forbid": (".dylib", ".so"),
        "native": ".pyd",
        "app": None,
        "launcher": "AVF.bat",
        "desktop": None,          # pythonnet + WebView2 cannot load on macOS
    },
    "linux-x64": {
        "pbs": "x86_64-unknown-linux-gnu",
        "pbs_sha": "64427febea27864d136db46c8efe968eb6fa5ca2813ce1dca4bb95aec31cb2e4",
        "uv": "x86_64-unknown-linux-gnu",
        "env": {},
        "site": "python/lib/python3.11/site-packages",
        "python": "python/bin/python3",
        "ffmpeg": [
            (f"{MR}/linux/amd64/1789931100_9.0.2/ffmpeg.zip",
             "fa8ecf4abbd290d98f7d188b8649cc6b391ae209a98452be955a15aab1909d7f"),
            (f"{MR}/linux/amd64/1789931100_9.0.2/ffprobe.zip",
             "3f428c49070be3d24ec338602b76d412e401ffcb8a5641ef0e729181a232fc32"),
        ],
        "forbid": (".pyd", ".dylib"),
        "native": ".so",
        "glibc": "2.35",          # Ubuntu 22.04 / Debian 12 — set by ffmpeg
        "app": None,
        "launcher": "avf.sh",
        "desktop": None,          # webkit2gtk is a system library, not a wheel
        # The type-2 AppImage runtime. Statically linked, so it carries its own
        # squashfuse and the image needs no libfuse2 on the user's machine. A
        # dated tag, not `continuous`: those assets are replaced in place, which
        # would break the pin the way it would break any other pinned download.
        "runtime": ("https://github.com/AppImage/type2-runtime/releases/download/"
                    "20251108/runtime-x86_64",
                    "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"),
    },
}

# Copied verbatim; the rest of the app arrives as a wheel. `assets/music` holds
# the CC0 beds — without it a bundle's music folder starts empty and every bed
# picker and mix preview has nothing to work with.
ASSETS = ("config", "prompts", "web/out", "assets")

# What every bundle must contain. Two roots: the interpreter and the launcher sit
# at the stage root, everything the app resolves through ROOT lives in
# site-packages (see the layout note at the top of this file).
REQUIRED = ("python", "desktop.py", "smoke.py")
REQUIRED_SITE = ("config/app.yaml", "prompts", "web/out/index.html",
                 # checked because an empty bed set is invisible until someone
                 # renders: the music picker just offers nothing
                 "assets/music/LICENSES.md",
                 # same reason: a missing icon is a generic grey app in the dock,
                 # which nobody notices until they are looking for it
                 "assets/icon/avf.icns")

LAUNCHERS = {
    # Contents/MacOS/AVF. Finder runs this without a Terminal, and living inside
    # a real bundle is what gives the process a dock icon and a menu bar.
    "AVF":     '#!/bin/sh\n# AVF launcher — double-click AVF.app in Finder.\n'
               'cd "$(dirname "$0")/../Resources"\nexec {py} desktop.py "$@"\n',
    "avf.sh":  '#!/bin/sh\n# AVF launcher.\ncd "$(dirname "$0")"\n'
               'exec {py} desktop.py "$@"\n',
    # pythonw.exe, not python.exe: no console, no flash. It also has no stdout,
    # so a crash is only visible in runtime/logs/desktop.log — which is exactly
    # why desktop.py writes one.
    "AVF.bat": '@echo off\r\nrem AVF launcher.\r\ncd /d "%~dp0"\r\n'
               'start "" {py} desktop.py %*\r\n',
}

# %k is the .desktop file's own path, so the entry works from wherever the
# folder was unzipped without baking in a build-machine path.
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=AVF
Comment=AI Video Factory
Exec=sh -c "cd $(dirname %k) && exec ./avf.sh"
Icon=avf
Terminal=false
Categories=AudioVideo;Video;
"""

# The AppImage gets its own copy of both. $APPDIR is the mount point the runtime
# sets, so nothing is relative to where the file happens to sit.
APPRUN = """#!/bin/sh
# AppImage entry point.
cd "${APPDIR:-$(dirname "$0")}" || exit 1
exec ./python/bin/python3 desktop.py "$@"
"""

# Exec=AppRun, not the zip's %k form: %k resolves to wherever the .desktop file
# itself lives, which is right while it sits next to avf.sh and wrong the moment
# desktop integration copies it into ~/.local/share/applications. AppRun is what
# the runtime and every integration tool resolve inside a mounted image.
APPIMAGE_DESKTOP = """[Desktop Entry]
Type=Application
Name=AVF
Comment=AI Video Factory
Exec=AppRun
Icon=avf
Terminal=false
Categories=AudioVideo;Video;
"""

# CFBundleExecutable is the one key Finder actually needs; CFBundleIconFile is
# the other one that shows up in the user's face. The .icns itself is copied into
# Contents/Resources by build(), and is generated by packaging/make_icon.py.
INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>AVF</string>
    <key>CFBundleIconFile</key><string>avf</string>
    <key>CFBundleIdentifier</key><string>dev.avf.console</string>
    <key>CFBundleName</key><string>AVF</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>{min_os}</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def sh(cmd: list[str], env: dict | None = None, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=cwd or REPO,
                   env={**os.environ, **(env or {})})


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, sha256: str) -> Path:
    """Fetch into the cache, refusing anything whose checksum does not match."""
    dest = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:12] + "-"
                    + url.split("/")[-1])
    if not dest.exists():
        print(f"↓ {url}")
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)
    if (got := digest(dest)) != sha256:
        dest.unlink()
        raise SystemExit(f"checksum mismatch — deleted {dest}\n"
                         f"  expected {sha256}\n  got      {got}")
    return dest


def extract(archive: Path, dest: Path, skip: tuple[str, ...] = ()) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            for m in t:
                if m.name.startswith(skip):
                    continue
                try:
                    t.extract(m, dest, filter="fully_trusted")
                except TypeError:      # filter= arrived in 3.11.4
                    t.extract(m, dest)


# The interpreter's own terminfo database. Skipped, for two reasons that both
# point the same way: the linux tarball carries it in both cases (terminfo/N/ and
# terminfo/n/), so on a case-insensitive filesystem — macOS, where the bundle is
# assembled — the symlink N/NCR260VT300WPP -> ../n/ncr260vt300wpp resolves onto
# itself and extraction dies with ELOOP. Nothing here needs it: ncurses falls back
# to the system database, which is present and complete on any real Linux.
TERMINFO = ("python/share/terminfo/",)


def extract_members(archive: Path, dest: Path, names: tuple[str, ...]) -> None:
    """Pull named files out of an archive that also carries docs and licenses."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            if Path(member).name in names and not member.endswith("/"):
                with z.open(member) as src, open(dest / Path(member).name, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def zip_dir(stage: Path, out: Path) -> Path:
    """Zip a bundle, keeping the executable bit.

    A plain zipfile/shutil.make_archive drops unix permissions, and python/bin/python3
    without +x is a bundle that cannot start after unzip. Fixed timestamps keep the
    archive byte-identical between runs.
    """
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(stage.rglob("*")):
            rel = path.relative_to(stage.parent)
            info = zipfile.ZipInfo(str(rel), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            if path.is_dir():
                info.external_attr = (0o755 << 16) | 0x10
                z.writestr(info, b"")
            else:
                info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
                with open(path, "rb") as src, z.open(info, "w") as dst:
                    shutil.copyfileobj(src, dst)
    return out


def _style_dmg_volume(mount: Path, app: str) -> None:
    """Make the mounted volume read like a product, not a folder: branded
    background, fixed icon positions, hidden window chrome, custom volume icon.

    Written directly as a .DS_Store (the same records electron-builder writes —
    bwsp window bounds, icvp icon-view prefs with the background alias, Iloc
    icon positions) rather than scripted through Finder: Finder automation needs
    a permission prompt on every build machine, and a library call does not.
    """
    try:
        from ds_store import DSStore
        import mac_alias
    except ImportError:
        raise SystemExit("ds_store is required to style the DMG — "
                         "pip install ds_store mac_alias")

    # custom volume icon: the icns plus Finder's has-custom-icon bit
    icns = mount / app / "Contents/Resources/avf.icns"
    if icns.is_file():
        shutil.copy2(icns, mount / ".VolumeIcon.icns")
        subprocess.run(["xattr", "-wx", "com.apple.FinderInfo",
                        "00" * 8 + "0400" + "00" * 22, str(mount)],
                       capture_output=True)

    bg = mount / ".background.png"
    alias = mac_alias.Alias.for_file(str(bg)).to_bytes()
    with DSStore.open(str(mount / ".DS_Store"), "w+") as d:
        d["."]["bwsp"] = {
            "ContainerShowSidebar": False, "PreviewPaneVisibility": False,
            "ShowPathbar": False, "ShowSidebar": False, "ShowStatusBar": False,
            "ShowTabView": False, "ShowToolbar": False, "SidebarWidth": 180,
            "WindowBounds": "{{400, 530}, {540, 380}}",
        }
        d["."]["icvl"] = (b"type", b"icnv")
        # the field set mirrors a working installer DMG record for record —
        # backgroundType in particular: 2 means "picture", 1 is ignored by
        # Finder (the background silently never draws), 0 is the plain colour
        d["."]["icvp"] = {
            "arrangeBy": "none", "backgroundType": 2,
            "backgroundColorRed": 1.0, "backgroundColorGreen": 1.0,
            "backgroundColorBlue": 1.0,
            "backgroundImageAlias": alias,
            "gridOffsetX": 0.0, "gridOffsetY": 0.0,
            "gridSpacing": 100.0, "iconSize": 80.0, "textSize": 12.0,
            "showItemInfo": False, "showIconPreview": False,
            "labelOnBottom": True,
            "scrollPositionX": 0.0, "scrollPositionY": 0.0,
            "viewOptionsVersion": 1,
        }
        d["."]["vSrn"] = (b"long", 1)
        # positions match the dashed arrow drawn into packaging/dmg-background.png
        d[app]["Iloc"] = (130, 220)
        d["Applications"]["Iloc"] = (410, 220)


def _app_version() -> str:
    import tomllib
    with open(REPO / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def dmg(stage: Path, app: str, out: Path) -> Path:
    """Wrap the .app in a drag-to-Applications disk image.

    The one step that cannot be cross-built: the bundle assembles on any host,
    but hdiutil only exists on macOS. The Applications symlink is what makes the
    mounted window a drag target instead of a folder the user has to reason
    about.

    Styled like a product installer (background art, icon layout, window bounds
    — see _style_dmg_volume): hdiutil creates a read-write image, the styling is
    written onto the mounted volume, then the image is converted to compressed
    UDZO. A one-shot UDZO create cannot carry a .DS_Store.
    """
    if not shutil.which("hdiutil"):
        raise SystemExit("hdiutil is missing — a macOS DMG has to be built on macOS")
    work = OUT / "dmg-src"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copytree(stage / app, work / app, symlinks=True)
    (work / "Applications").symlink_to("/Applications")
    # hidden file at the volume root, the layout proven installers use
    shutil.copy2(REPO / "packaging" / "dmg-background.png", work / ".background.png")

    tmp = OUT / "avf-styling.dmg"
    tmp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    volname = f"AVF {_app_version()}-arm64"
    sh(["hdiutil", "create", "-volname", volname, "-srcfolder", str(work),
        "-ov", "-format", "UDRW", str(tmp)])
    # Style the volume at its real mount point. The background alias records
    # where the volume lived when the .DS_Store was written — a temporary
    # mount dir leaves that path baked in, and Finder on the user's machine
    # fails to resolve it, so the background silently never draws. Mounted at
    # /Volumes/<volname> the alias points where every user's mount lands too.
    mount = Path("/Volumes") / volname
    if mount.exists():
        if not any(mount.iterdir()):
            mount.rmdir()  # stale empty dir from a crashed build would force "... 1"
        else:
            raise SystemExit(
                f"a volume is already mounted at {mount} — eject it first (it is "
                f"another AVF image; taking the name would land this styling on "
                f"the wrong disk, and ejecting by path could hit that one instead)")
    proc = subprocess.run(["hdiutil", "attach", str(tmp), "-nobrowse"],
                          check=True, capture_output=True, text=True, cwd=REPO)
    # attach prints "<dev> … <mountpoint>"; detach by DEVICE, never by path —
    # a path detach with a colliding name could eject somebody else's volume.
    dev = proc.stdout.split()[0]
    try:
        if not (mount.is_dir() and os.access(mount, os.W_OK)):
            raise SystemExit(f"{mount} is not our writable styling volume — aborting")
        _style_dmg_volume(mount, app)
    finally:
        sh(["hdiutil", "detach", dev, "-quiet"])
    sh(["hdiutil", "convert", str(tmp), "-format", "UDZO", "-o", str(out)])
    tmp.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    return out


def _check_appimage(path: Path) -> int:
    """Read back where the runtime will look for its filesystem, and prove the
    squashfs is there. That offset is e_shoff + e_shnum*e_shentsize; a
    concatenation off by a byte still produces a file that looks like a valid
    AppImage and only fails on the user's machine, as a mount error."""
    import struct

    with open(path, "rb") as f:
        head = f.read(64)
        e_shoff, = struct.unpack_from("<Q", head, 0x28)
        e_shentsize, e_shnum = struct.unpack_from("<HH", head, 0x3A)
        offset = e_shoff + e_shnum * e_shentsize
        f.seek(offset)
        if f.read(4) != b"hsqs":
            raise SystemExit(
                f"{path.name}: no squashfs at the runtime's offset ({offset}) — "
                "the image would mount as an empty directory")
    return offset


def appimage(stage: Path, out: Path, runtime: tuple[str, str]) -> Path:
    """Wrap the Linux stage in a type-2 AppImage.

    appimagetool is a Linux-only binary and cannot run on a macOS or Windows
    build host, so the image is assembled the way appimagetool assembles it: the
    pinned runtime binary followed by a squashfs of the AppDir. The runtime finds
    that filesystem at e_shoff + e_shnum*e_shentsize, which is exactly the
    runtime file's own length — true of both published architectures, and checked
    again on the finished image — so a plain concatenation lands the squashfs
    where the runtime looks for it.

    The AppDir is a hard-link copy of the stage: the only differences are the
    entry point and the desktop file, so a second ~350 MB copy buys nothing.
    """
    if not shutil.which("mksquashfs"):
        raise SystemExit("mksquashfs is required for the AppImage — "
                         "brew install squashfs / apt install squashfs-tools")
    appdir = OUT / "appimage-src"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(stage, appdir, copy_function=os.link, symlinks=True)
    (appdir / "AppRun").write_text(APPRUN)
    (appdir / "AppRun").chmod(0o755)
    # Unlink before writing. This one is a hard link into the stage, and opening
    # it for writing truncates the shared inode — which would silently rewrite the
    # copy the zip ships, replacing its Exec= with an AppRun that is not there.
    (appdir / "avf.desktop").unlink()
    (appdir / "avf.desktop").write_text(APPIMAGE_DESKTOP)

    fs = OUT / "avf-appimage.squashfs"
    fs.unlink(missing_ok=True)
    # -all-root: the image mounts read-only, and files owned by the build user's
    # uid show up as nobody on the user's machine. gzip rather than zstd: the
    # static runtime reads squashfs in userspace, and gzip is what every
    # squashfuse build carries.
    sh(["mksquashfs", str(appdir), str(fs), "-all-root", "-noappend",
        "-no-progress", "-comp", "gzip"])

    out.unlink(missing_ok=True)
    with open(out, "wb") as dst:
        for part in (download(*runtime), fs):
            with open(part, "rb") as src:
                shutil.copyfileobj(src, dst)
    out.chmod(0o755)
    print(f"  ✓ squashfs at offset {_check_appimage(out)}")

    fs.unlink(missing_ok=True)
    shutil.rmtree(appdir, ignore_errors=True)
    return out


WEBVIEW2_BOOTSTRAP_URL = "https://go.microsoft.com/fwlink/?linkid=2124703"


def compile_win_launcher(stage: Path) -> None:
    """AVF.exe: a 38 KB launcher so no Windows user ever meets a .bat.

    Cross-compiled with mingw-w64 (brew install mingw-w64): pythonw.exe runs
    with no console flash, AVF's icon is embedded, and the process waits so a
    taskbar pin behaves like an app. Without mingw the .bat stays the launcher
    and every other artifact still builds.
    """
    windres = shutil.which("x86_64-w64-mingw32-windres")
    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if not (windres and gcc):
        print("  (mingw-w64 not installed — keeping AVF.bat as the launcher)")
        return
    obj = OUT / "win-launcher.o"
    sh([windres, str(REPO / "packaging" / "win-launcher.rc"),
        "-O", "coff", "-o", str(obj)])
    sh([gcc, str(REPO / "packaging" / "win-launcher.c"), str(obj),
        "-o", str(stage / "AVF.exe"), "-municode", "-mwindows", "-s", "-static"])
    obj.unlink()


def fetch_webview2_bootstrapper(stage: Path) -> None:
    """The WebView2 evergreen bootstrapper (~2 MB), bundled for the installer.
    (fwlink 2124701 turned out to be the 212 MB standalone — kept out.)

    pywebview renders through WebView2; current Win10/11 have it, older or
    stripped-down machines do not, and the installer runs this silently when
    the registry says so. No SHA pin: Microsoft rotates the binary behind the
    fwlink — the installer only ever runs it from Microsoft's own endpoint.
    """
    import urllib.request

    dest = stage / "WebView2Bootstrapper.exe"
    if dest.exists():
        return
    req = urllib.request.Request(
        WEBVIEW2_BOOTSTRAP_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if dest.stat().st_size > 5_000_000:
        dest.unlink()
        raise SystemExit("WebView2 bootstrapper looks like the standalone "
                         "installer (too big) — refusing to bundle it")
    if dest.stat().st_size < 500_000:
        dest.unlink()
        raise SystemExit("WebView2 bootstrapper download looks wrong — refusing to bundle it")


def _win_header_bmp(path: Path) -> None:
    """The 150x57 installer-header art: white ground, teal mark, AVF wordmark
    — the electron-builder look, drawn here so no binary asset is committed."""
    from PIL import Image, ImageDraw

    W, H = 150, 57
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    x, y, size = 8, 12, 33
    d.rounded_rectangle([x, y, x + size, y + size], radius=8, fill=(45, 212, 191))
    d.polygon([(x + 13, y + 10), (x + 25, y + 16.5), (x + 13, y + 23)],
              fill=(10, 13, 18))
    font = None
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(f, 15)
            break
        except OSError:
            continue
    d.text((x + size + 9, y + 8), "AVF", fill=(10, 13, 18), font=font)
    d.text((x + size + 9, y + 22), "CONSOLE", fill=(45, 212, 191), font=font)
    img.save(path, "BMP")


def win_setup(stage: Path, out: Path) -> Path:
    """Setup.exe via NSIS (brew install makensis) — the one-click installer
    ZenPilot ships: progress, "Run AI Video Factory" checked at the end,
    per-user install, desktop shortcut, uninstaller in Apps & Features."""
    makensis = shutil.which("makensis")
    if not makensis:
        raise SystemExit("makensis is required for the Windows setup — "
                         "brew install makensis")
    header = OUT / "win-header.bmp"
    _win_header_bmp(header)
    nsi = OUT / "win-setup.gen.nsi"
    nsi.write_text((REPO / "packaging" / "win-setup.nsi").read_text()
                   .replace("@STAGE@", str(stage))
                   .replace("@OUT@", str(out / "avf-windows-x64-setup.exe"))
                   .replace("@ICO@", str(REPO / "assets" / "icon" / "avf.ico"))
                   .replace("@HEADER@", str(header))
                   .replace("@VERSION@", _app_version()))
    sh([makensis, "-V2", str(nsi)])
    nsi.unlink()
    return out / "avf-windows-x64-setup.exe"


def _glibc_floor(binary: Path) -> tuple[int, int]:
    """Highest GLIBC_x.y the binary's dynamic symbols ask for, (0,0) if static."""
    found = re.findall(rb"GLIBC_(\d+\.\d+)", binary.read_bytes())
    vers = {tuple(int(part) for part in m.decode().split(".")) for m in found}
    return max(vers, default=(0, 0))


def _roots(stage: Path, t: dict) -> tuple[Path, Path]:
    """(content root, entry-point dir). Only macOS splits them — see the docstring."""
    if t["app"]:
        return (stage / t["app"] / "Contents" / "Resources",
                stage / t["app"] / "Contents" / "MacOS")
    return stage, stage


def verify(stage: Path, t: dict) -> None:
    """Checks that do not need to execute the target: layout and native modules."""
    content, launchers = _roots(stage, t)
    missing = [r for r in REQUIRED if not (content / r).exists()]

    site = content / t["site"]
    missing += [f"{t['site']}/{r}" for r in REQUIRED_SITE
                if not (site / r).exists()]
    if missing:
        raise SystemExit(f"{stage.name}: missing {', '.join(missing)}")

    # The entry point has to be executable: zip_dir is what preserves the bit,
    # and a bundle whose launcher lost it looks fine until someone unzips it.
    launcher = launchers / t["launcher"]
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise SystemExit(f"{stage.name}: {t['launcher']} missing or not executable")

    vendor = site / "vendor"
    for binary in ("ffmpeg", "ffprobe"):
        if not any(p.stem == binary for p in vendor.iterdir()):
            raise SystemExit(f"{stage.name}: vendor/{binary} not found")

    # The ffmpeg we ship, not the wheels, sets the Linux floor: martin-riedl's
    # builds ask for GLIBC_2.35 (Ubuntu 22.04 / Debian 12) while the wheels stop
    # at 2.29. Checked rather than trusted, so swapping the ffmpeg source for one
    # built on a newer distro fails here instead of on a user's older machine.
    if want := t.get("glibc"):
        want_v = tuple(int(x) for x in want.split("."))
        for binary in ("ffmpeg", "ffprobe"):
            p = next(vendor.glob(f"{binary}*"))
            if (got := _glibc_floor(p)) > want_v:
                raise SystemExit(
                    f"{stage.name}: {binary} needs GLIBC_{got[0]}.{got[1]}, above "
                    f"the declared floor {want} — find an older build or raise it")

    leaked = [p.relative_to(site) for ext in t["forbid"]
              for p in site.rglob(f"*{ext}")]
    if leaked:
        raise SystemExit(f"{stage.name}: host binaries leaked in: {leaked[:5]}")

    if not list(site.rglob(f"*{t['native']}")):
        raise SystemExit(f"{stage.name}: no {t['native']} files — the wheels are "
                         "for the wrong platform")
    if t["desktop"]:
        if not (site / t["desktop"]).exists():
            raise SystemExit(f"{stage.name}: {t['desktop']} missing")
    print(f"  ✓ {stage.name}: layout ok")


def build(target: str) -> Path:
    t = TARGETS[target]
    stage = OUT / f"avf-{target}"
    shutil.rmtree(stage, ignore_errors=True)
    print(f"\n=== {target} → {stage}")

    # 1. interpreter for the target, straight from the prebuilt tarball. On macOS
    #    it lands inside the .app's Resources — that is the content root there.
    content, launchers = _roots(stage, t)
    extract(download(f"{PBS_BASE}/cpython-{PBS_PY}+{PBS_TAG}-{t['pbs']}"
                     f"-install_only_stripped.tar.gz", t["pbs_sha"]), content,
            skip=TERMINFO)
    site = content / t["site"]

    # 2. wheels. --only-binary keeps a package without a target wheel from being
    #    compiled on the host, which would silently produce a host-platform binary.
    #    proxy-tools (pywebview's one non-wheel dependency) is pure Python, so the
    #    single sdist is safe and is the only way to get the desktop shell.
    sh(["uv", "pip", "install", "--target", str(site),
        "--python-platform", t["uv"], "--python-version", "3.11",
        "--only-binary", ":all:", "--no-binary", "proxy-tools",
        "-r", "pyproject.toml", "--extra", "desktop"], t["env"])
    sh(["uv", "pip", "install", "--target", str(site),
        "--python-platform", t["uv"], "--python-version", "3.11",
        "--no-deps", "."], t["env"])
    shutil.rmtree(site / "test", ignore_errors=True)  # phonemizer ships its tests

    # 3. ffmpeg/ffprobe + the font drawtext needs (Windows has no DejaVu, and
    #    ffmpeg without fontfile has nothing to fall back on there)
    vendor = site / "vendor"
    exe = ".exe" if t["launcher"] == "AVF.bat" else ""
    for url, sha in t["ffmpeg"]:
        extract_members(download(url, sha), vendor, (f"ffmpeg{exe}", f"ffprobe{exe}"))
    for binary in vendor.iterdir():
        binary.chmod(0o755)

    fonts = vendor / "fonts"
    fonts.mkdir(exist_ok=True)
    unzipped = OUT / "dejavu"
    shutil.rmtree(unzipped, ignore_errors=True)
    extract(download(DEJAVU, "7576310b219e04159d35ff61dd4a4ec4cdba4f35c00e002a136f00e96a908b0a"),
            unzipped)
    for name in ("ttf/DejaVuSans-Bold.ttf", "LICENSE"):
        shutil.copy2(unzipped / "dejavu-fonts-ttf-2.37" / name,
                     fonts / (Path(name).name if name.endswith("ttf")
                              else "DejaVu-LICENSE.txt"))
    shutil.rmtree(unzipped, ignore_errors=True)

    # 4. resources that are not part of the wheel, + the entry point
    for asset in ASSETS:
        dst = site / asset
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(REPO / asset, dst)
    # Both go to the content root. smoke.py in particular has to sit inside the
    # bundle: its sys.path bootstrap prefers a source checkout when it finds one,
    # so run from the repo it would test the repo's ffmpeg instead of the bundled
    # one — which is the whole thing it is supposed to check.
    for script in ("desktop.py", "smoke.py"):
        shutil.copy2(REPO / "packaging" / script, content / script)

    if t["launcher"] == "AVF.bat":
        # a real exe launcher and the WebView2 bootstrapper exist only for
        # windows; without mingw the .bat stays the launcher
        compile_win_launcher(launchers)
        fetch_webview2_bootstrapper(content)

    launchers.mkdir(parents=True, exist_ok=True)
    launcher = launchers / t["launcher"]
    launcher.write_text(LAUNCHERS[t["launcher"]].format(py=t["python"]))
    launcher.chmod(0o755)
    if t["app"]:
        # LSMinimumSystemVersion is derived, not restated: it has to match the
        # deployment target the wheels were resolved against.
        (stage / t["app"] / "Contents" / "Info.plist").write_text(
            INFO_PLIST.format(min_os=t["env"]["MACOSX_DEPLOYMENT_TARGET"]))
        # CFBundleIconFile resolves against Contents/Resources, not site-packages.
        shutil.copy2(REPO / "assets" / "icon" / "avf.icns",
                     content / "avf.icns")
    elif t["launcher"] == "AVF.bat":
        # Windows: a .bat cannot carry an icon, so the .ico just sits beside it
        # for the user to pin to the taskbar. The ponytail note on the launcher
        # covers the real fix (a compiled .exe with windres).
        shutil.copy2(REPO / "assets" / "icon" / "avf.ico", stage / "AVF.ico")
    if t["launcher"] == "avf.sh":
        (content / "avf.desktop").write_text(DESKTOP_ENTRY)
        # desktop.py installs this into the XDG icon theme on first launch, which
        # is the only way `Icon=avf` resolves in a menu.
        shutil.copy2(REPO / "assets" / "icon" / "avf-512.png", content / "avf.png")

    verify(stage, t)
    return stage


# The same list the Makefile uses. It lives here as well because build.py is the
# one command that has to work on Windows, where there is no make to run `ui`.
UI_SOURCES = ("app/**/*", "next.config.js", "package.json")


def web_is_stale() -> bool:
    """web/out is gitignored, so a fresh clone has none — and a stale one ships
    the wrong UI. Building it here is what makes this a single command."""
    web = REPO / "web"
    index = web / "out" / "index.html"
    if not index.is_file():
        return True
    newest = max((p.stat().st_mtime for pat in UI_SOURCES
                  for p in web.glob(pat) if p.is_file()), default=0.0)
    return newest > index.stat().st_mtime


def build_web() -> None:
    if not web_is_stale():
        print("  ✓ web/out is current")
        return
    if not shutil.which("npm"):
        raise SystemExit("web/out is missing or stale and npm is not on PATH — "
                         "install Node.js, then rerun")
    print("  ▶ building web/out (npm)…")
    if not (REPO / "web" / "node_modules").is_dir():
        sh(["npm", "install"], cwd=REPO / "web")
    sh(["npm", "run", "build"], cwd=REPO / "web")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="+", choices=[*TARGETS, "all"])
    args = ap.parse_args()
    wanted = list(TARGETS) if "all" in args.targets else args.targets

    if not shutil.which("uv"):
        raise SystemExit("uv is required — brew install uv / pipx install uv")
    build_web()
    for script in ("desktop.py", "smoke.py"):
        if not (REPO / "packaging" / script).is_file():
            raise SystemExit(f"packaging/{script} is missing")

    for target in wanted:
        stage = build(target)
        zipped = zip_dir(stage, OUT / f"avf-{target}.zip")
        print(f"  ✓ {zipped.relative_to(REPO)} "
              f"({zipped.stat().st_size / 1e6:.0f} MB)")
        if TARGETS[target]["launcher"] == "AVF.bat":
            # win-setup.nsi hard-codes AVF.exe for its shortcuts and its "Run AI
            # Video Factory" tick, so an installer assembled without the mingw
            # launcher would install a menu entry pointing at nothing.
            if not shutil.which("makensis"):
                print("  ⚠ avf-windows-x64-setup.exe skipped — makensis not found "
                      "(brew install makensis)")
            elif not (stage / "AVF.exe").is_file():
                print("  ⚠ avf-windows-x64-setup.exe skipped — AVF.exe was not "
                      "built, so the installer's shortcuts would dangle "
                      "(brew install mingw-w64)")
            else:
                setup = win_setup(stage, OUT)
                print(f"  ✓ {setup.relative_to(REPO)} "
                      f"({setup.stat().st_size / 1e6:.0f} MB)")
        # A convenience wrapper around the .app, not a second artifact: hdiutil is
        # macOS-only, so `all` on another host still ships the bundle in the zip.
        app = TARGETS[target]["app"]
        if app and shutil.which("hdiutil"):
            image = dmg(stage, app, OUT / f"avf-{target}.dmg")
            print(f"  ✓ {image.relative_to(REPO)} "
                  f"({image.stat().st_size / 1e6:.0f} MB)")
        # Same story: assembled rather than run, so it cross-builds too — the
        # runtime is a pinned download and only mksquashfs has to exist here.
        # Skipped rather than fatal when it is absent, so that `all` still
        # completes on a host without it (Windows has no squashfs-tools at all).
        runtime = TARGETS[target].get("runtime")
        if runtime:
            if shutil.which("mksquashfs"):
                image = appimage(stage, OUT / f"avf-{target}.AppImage", runtime)
                print(f"  ✓ {image.relative_to(REPO)} "
                      f"({image.stat().st_size / 1e6:.0f} MB)")
            else:
                print(f"  ⚠ avf-{target}.AppImage skipped — mksquashfs not found "
                      "(brew install squashfs / apt install squashfs-tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
