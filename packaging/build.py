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
            ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
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


def sh(cmd: list[str], env: dict | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=REPO, env={**os.environ, **(env or {})})


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

    bg = mount / ".background" / "background.png"
    alias = mac_alias.Alias.for_file(str(bg)).to_bytes()
    with DSStore.open(str(mount / ".DS_Store"), "w+") as d:
        d["."]["bwsp"] = {
            "ContainerShowSidebar": False, "PreviewPaneVisibility": False,
            "ShowPathbar": False, "ShowSidebar": False, "ShowStatusBar": False,
            "ShowTabView": False, "ShowToolbar": False, "SidebarWidth": 180,
            "WindowBounds": "{{260, 200}, {660, 420}}",
        }
        d["."]["icvl"] = (b"type", b"icnv")
        d["."]["icvp"] = {
            "arrangeBy": "none", "backgroundType": 1,
            "backgroundColorRed": 10 / 255, "backgroundColorGreen": 13 / 255,
            "backgroundColorBlue": 18 / 255,
            "backgroundImageAlias": alias,
            "gridSpacing": 100, "iconSize": 96, "textSize": 13,
            "showItemInfo": False, "labelOnBottom": True,
        }
        d["."]["vSrn"] = (b"long", 1)
        # positions match the slots drawn into packaging/dmg-background.png
        d[app]["Iloc"] = (160, 240)
        d["Applications"]["Iloc"] = (470, 240)


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
    bg = work / ".background"
    bg.mkdir()
    shutil.copy2(REPO / "packaging" / "dmg-background.png", bg / "background.png")

    tmp = OUT / "avf-styling.dmg"
    tmp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    sh(["hdiutil", "create", "-volname", "AVF", "-srcfolder", str(work),
        "-ov", "-format", "UDRW", str(tmp)])
    mount = OUT / "dmg-mount"
    shutil.rmtree(mount, ignore_errors=True)
    mount.mkdir()
    sh(["hdiutil", "attach", str(tmp), "-mountpoint", str(mount),
        "-nobrowse", "-quiet"])
    try:
        _style_dmg_volume(mount, app)
    finally:
        sh(["hdiutil", "detach", str(mount), "-quiet"])
        shutil.rmtree(mount, ignore_errors=True)
    sh(["hdiutil", "convert", str(tmp), "-format", "UDZO", "-o", str(out)])
    tmp.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    return out


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="+", choices=[*TARGETS, "all"])
    args = ap.parse_args()
    wanted = list(TARGETS) if "all" in args.targets else args.targets

    if not shutil.which("uv"):
        raise SystemExit("uv is required — brew install uv / pipx install uv")
    if not (REPO / "web" / "out" / "index.html").is_file():
        raise SystemExit("web/out is missing or stale — run `npm run build` in web/")
    for script in ("desktop.py", "smoke.py"):
        if not (REPO / "packaging" / script).is_file():
            raise SystemExit(f"packaging/{script} is missing")

    for target in wanted:
        stage = build(target)
        zipped = zip_dir(stage, OUT / f"avf-{target}.zip")
        print(f"  ✓ {zipped.relative_to(REPO)} "
              f"({zipped.stat().st_size / 1e6:.0f} MB)")
        # A convenience wrapper around the .app, not a second artifact: hdiutil is
        # macOS-only, so `all` on another host still ships the bundle in the zip.
        app = TARGETS[target]["app"]
        if app and shutil.which("hdiutil"):
            image = dmg(stage, app, OUT / f"avf-{target}.dmg")
            print(f"  ✓ {image.relative_to(REPO)} "
                  f"({image.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
