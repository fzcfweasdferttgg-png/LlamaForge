#!/usr/bin/env python3
"""Capture per-tab dashboard screenshots into docs/content/img/ (maintainer tool).

Assumes the LlamaForge dashboard is running locally (default localhost:8090)
with representative state. Requires a Chrome/Chromium binary on PATH (set
$CHROME to override). Never run in CI. Mirrors the headless recipe in
docs/hero.html.

Usage: python tools/shoot.py            # capture all shots in the manifest
       python tools/shoot.py models     # capture one shot by name
"""
import os, shutil, subprocess, sys, tempfile, time
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(_ROOT, "docs", "content", "img")
BASE = os.environ.get("LF_URL", "http://localhost:8090")
SIZE = (1280, 900)

# shot name -> the tab/hash the dashboard opens for that view.
# Content pages reference docs/img/<name>.png using these names.
MANIFEST = {
    "overview": "/",
    "models": "/#models",
    "discover": "/#discover",
    "build": "/#build",
    "setup": "/#setup",
    "help": "/#help",
}


def _chrome():
    for c in (os.environ.get("CHROME"), "chrome", "chromium", "google-chrome",
              "google-chrome-stable"):
        if c and shutil.which(c):
            return shutil.which(c)
    sys.exit("No Chrome/Chromium found; set $CHROME to the binary path.")


def _up():
    try:
        urllib.request.urlopen(BASE, timeout=2).read(1)
        return True
    except Exception:
        return False


def shoot(name):
    if name not in MANIFEST:
        sys.exit("unknown shot %r; known: %s" % (name, ", ".join(MANIFEST)))
    os.makedirs(IMG, exist_ok=True)
    url = BASE + MANIFEST[name]
    out = os.path.join(IMG, name + ".png")
    prof = tempfile.mkdtemp()
    try:
        subprocess.run([_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=2",
                        "--window-size=%d,%d" % SIZE, "--user-data-dir=" + prof,
                        "--virtual-time-budget=4000",
                        "--screenshot=" + out, url], check=True)
        print("wrote", out)
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def main():
    if not _up():
        sys.exit("Dashboard not reachable at %s — start it first." % BASE)
    names = sys.argv[1:] or list(MANIFEST)
    for nm in names:
        shoot(nm); time.sleep(0.5)


if __name__ == "__main__":
    main()
