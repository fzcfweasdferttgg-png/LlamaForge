"""Install pi for the user, inside LlamaForge's own folder.

pi is the coding agent by Mario Zechner (MIT, (c) 2025),
https://github.com/earendil-works/pi - npm package @earendil-works/pi-coding-agent.
LlamaForge doesn't bundle or modify it: on request it runs the user's own
Node.js/npm to install the published package into <root>/agents/pi (never -g),
and pirun.locate() prefers that copy over a pi on PATH. Updating is the same
install again (@latest); removing deletes the folder.

npm runs as `node npm-cli.js`, never the npm.cmd shim (cmd.exe re-parses
arguments), with --ignore-scripts: nothing in pi's dependency tree needs an
install script, so no third-party code runs at install time.

Node.js itself goes through prereqs (winget/choco/brew; Linux gets a hint).
"""
import json
import os
import re
import shutil
import subprocess
import threading

import osplat
import pirun
import prereqs

MIN_NODE = (22, 19, 0)
SPEC = "/".join(pirun.PACKAGE) + "@latest"
TIMEOUT = 900
BUSY = "pi is already being installed or removed"
_NO_WINDOW = 0x08000000
_lock = threading.Lock()
_last = {}
_worker = None


def parse_version(text):
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def new_enough(v):
    return bool(v) and v >= MIN_NODE


def _run(cmd, env=None, timeout=None):
    """(returncode, combined output); never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW if osplat.IS_WIN else 0)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"
    except OSError as e:
        return -1, str(e)


def node_info(which=shutil.which, run=_run):
    node = which("node") or ""
    if not node:
        return {"path": "", "version": "", "ok": False}
    v = parse_version(run([node, "--version"], timeout=15)[1])
    return {"path": node, "version": ".".join(map(str, v)) if v else "", "ok": new_enough(v)}


def npm_cli(node, which=shutil.which):
    """npm's own script for this node: beside it (Windows zip/installer), under
    <prefix>/lib (POSIX), else wherever the npm on PATH points."""
    tail = os.path.join("node_modules", "npm", "bin", "npm-cli.js")
    cands = []
    for n in dict.fromkeys((node, os.path.realpath(node))):
        d = os.path.dirname(n)
        cands += [os.path.join(d, tail), os.path.join(os.path.dirname(d), "lib", tail)]
    npm = which("npm")
    if npm:
        real = os.path.realpath(npm)
        if os.path.basename(real) == "npm-cli.js":
            cands.append(real)
        cands.append(os.path.join(os.path.dirname(npm), tail))
    return next((c for c in cands if os.path.isfile(c)), None)


def installed():
    """Version of the managed copy, or ""."""
    pj = os.path.join(pirun.MANAGED_DIR, "node_modules", *pirun.PACKAGE, "package.json")
    try:
        with open(pj, encoding="utf-8") as f:
            v = json.load(f).get("version")
    except (OSError, ValueError, AttributeError):
        return ""
    return v if isinstance(v, str) else ""


def busy():
    return _lock.locked()


def install(which=shutil.which, run=_run):
    """Install (or update) pi into the managed folder. (ok, log); log is BUSY
    when another install/remove is running."""
    if not _lock.acquire(blocking=False):
        return False, BUSY
    try:
        node = node_info(which, run)
        if not node["path"]:
            return False, "Node.js isn't installed. pi needs Node.js 22.19 or newer."
        if not node["ok"]:
            return False, (f"Node.js {node['version'] or '(unknown version)'} is too old: "
                           f"pi needs {'.'.join(map(str, MIN_NODE))} or newer.")
        npm = npm_cli(node["path"], which)
        if not npm:
            return False, f"Couldn't find npm next to {node['path']}. Reinstall Node.js (it ships npm)."
        os.makedirs(pirun.MANAGED_DIR, exist_ok=True)
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(node["path"]) + os.pathsep + env.get("PATH", "")
        env["NO_UPDATE_NOTIFIER"] = "1"
        cmd = [node["path"], npm, "install", "--prefix", pirun.MANAGED_DIR, SPEC,
               "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"]
        rc, out = run(cmd, env=env, timeout=TIMEOUT)
        out = out.strip()
        if rc != 0:
            return False, f"npm failed (exit {rc}):\n{out[-4000:]}"
        v = installed()
        if not v:
            return False, "npm finished but pi isn't there.\n" + out[-4000:]
        return True, f"pi {v} installed in {pirun.MANAGED_DIR}" + (f"\n{out[-2000:]}" if out else "")
    finally:
        _lock.release()


def remove():
    """Delete the managed copy (a pi installed some other way is untouched)."""
    if not _lock.acquire(blocking=False):
        return False, BUSY
    try:
        if os.path.isdir(pirun.MANAGED_DIR):
            shutil.rmtree(pirun.MANAGED_DIR)
        return True, "removed"
    except OSError as e:
        return False, f"couldn't remove {pirun.MANAGED_DIR}: {e}"
    finally:
        _lock.release()


def start(action, which=shutil.which, run=_run):
    """Run install/remove on a thread (npm can take minutes); the outcome
    lands in last(). False when one is already running."""
    global _worker
    fn = {"install": lambda: install(which, run), "remove": remove}.get(action)
    if not fn:
        raise ValueError(f"unknown action: {action}")
    if busy() or (_worker and _worker.is_alive()):
        return False

    def go():
        ok, log = fn()
        if log is not BUSY:
            _last.clear()
            _last.update(action=action, ok=ok, log=log)

    _worker = threading.Thread(target=go, name=f"pi-{action}", daemon=True)
    _worker.start()
    return True


def wait(timeout=None):
    if _worker:
        _worker.join(timeout)


def last():
    return dict(_last)


def status(pi_bin="", which=shutil.which, run=_run):
    spec = prereqs.OPTIONAL["node"]
    hint = ""
    if osplat.IS_LINUX:
        hint = osplat.linux_install_hint(osplat.linux_pkg_manager(), spec["pkg"]) or ""
    return {
        "node": node_info(which, run),
        "min_node": ".".join(map(str, MIN_NODE)),
        "managed": installed(),
        "active": pirun.locate_how(pi_bin, which=which)[1],
        "busy": busy() or bool(_worker and _worker.is_alive()),
        "last": last(),
        "node_installable": prereqs._can_auto_install(spec),
        "hint": hint,
        "node_url": spec["url"],
    }
