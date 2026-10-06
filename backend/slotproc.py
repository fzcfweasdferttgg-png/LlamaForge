"""Process slots: one model in its own `llama-server -m` process.

The router serves every model with one binary. A model pinned to another build
(config.json model_builds) runs here instead: ik_llama.cpp has no router mode
at all, and a model can need a newer or older llama.cpp than the router's.
slotctl.SlotManager plans and places these exactly like router slots; this
module only owns the processes.

* The section's knobs become argv through *that binary's* --help, matched by
  any spelling the two builds share (models.ini says n-gpu-layers, ik only
  knows --gpu-layers). A false flag becomes its no- form, llama.cpp's own
  preset rule (common_preset::to_args). What the build lacks is dropped and
  named, never passed on to make it exit with "unknown argument". A value
  that only restates the router build's default asks for nothing to drop.
* Each process binds 127.0.0.1 on a port from slot_port_base up, logs to
  logs/slot-<model>.log, and is recorded in logs/slotprocs.json. A restarted
  panel adopts the records whose PID is still a llama process; stop.ps1 /
  stop.sh (procs.py) stop them by the same rule. Never by name.
* State comes from /health (llama-server answers 503 while it loads) and the
  exit code; the caller decides how long a load may take.

Pure stdlib.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import logfiles
import osplat
import procs

PORT_BASE = 8100
PORT_SPAN = 100
STOP_TIMEOUT_S = 15
RECORDS = procs.SLOTPROCS             # stop.ps1 / stop.sh read it too (procs.py)
_CREATE_NO_WINDOW = 0x08000000

# Set by the manager itself, or meaningless for one process serving one model.
OWNED = frozenset({
    "model", "host", "port", "api-key", "api-key-file", "alias", "metrics",
    "models-dir", "models-preset", "models-max", "models-autoload", "no-models-autoload",
    "hf-repo", "hf-file", "hf-token", "hf-repo-draft", "hf-repo-v", "path",
    "ssl-key-file", "ssl-cert-file", "cors-origins", "offline", "help", "usage",
    "version", "completion-bash", "cache-list", "list-devices"})
# Keys the router reads from a preset that are not CLI arguments (server README).
PRESET_ONLY = frozenset({"load-on-startup", "stop-timeout", "dedup-cache-models"})
# common_arg_utils::is_falsey
_FALSEY = ("0", "false", "off", "no")


# ---------------------------------------------------------------- argv

def _truthy(v):
    return str(v).strip().lower() not in _FALSEY


def _find(items, spellings, want_bool=None):
    for it in items:
        if want_bool is not None and (it.get("type") == "bool") != want_bool:
            continue
        if spellings & set(it.get("flags") or ()):
            return it
    return None


def _long(item, spellings=None):
    """The item's first --long spelling (within `spellings`, when given)."""
    flags = [f for f in item["flags"] if spellings is None or f in spellings]
    return next((f for f in flags if f.startswith("--")), flags[0] if flags else None)


def _restates_default(item, value, on=None):
    """True when `value` (or, for a flag, the wanted state `on`) is what the
    router build does anyway, per its --help "(default: ...)"."""
    d = str((item or {}).get("default") or "").strip().strip("'\"").lower()
    if not d:
        return False
    if on is None:
        return d == value.strip().strip("'\"").lower()
    if d in ("enabled", "true", "on", "1"):
        return on
    if d in ("disabled", "false", "off", "0"):
        return not on
    return False


# One value, spelled by mainline and by ik_llama.cpp (both --help lists).
VALUE_ALIASES = {
    "spec-type": {"draft-mtp": "mtp", "draft-simple": "draft", "draft-dflash": "dflash"},
}
_WORDS = re.compile(r"[\w.-]+(?:,[\w.-]+)*")


def _fit_value(value, d):
    """`value` as the build whose item is `d` spells it; None when that build
    lists what it takes and this isn't among it. An unlisted value, or one with
    a payload (ik: mtp:n_max=1), is the build's to judge."""
    known = d.get("values")
    if not known or not _WORDS.fullmatch(value):
        return value
    parts = value.split(",")
    if len(parts) > 1 and not (d.get("type") == "enum" and "," in (d.get("placeholder") or "")):
        return None                                   # a list, where the build takes one value
    alias = VALUE_ALIASES.get(d.get("key"), {})
    alias = {**alias, **{b: a for a, b in alias.items()}}
    out = []
    for p in parts:
        p = p if p in known else alias.get(p)
        if p not in known:
            return None
        out.append(p)
    return ",".join(out)


def _polarities(flags):
    """({positive spellings}, {no- spellings}) for every flag in `flags`."""
    pos, neg = set(), set()
    for f in flags:
        dash = f[:len(f) - len(f.lstrip("-"))]
        name = f.lstrip("-")
        base = name[3:] if name.startswith("no-") else name
        pos.add(dash + base)
        neg.add(dash + "no-" + base)
    return pos, neg


def translate(settings, dst, src=()):
    """(argv flags, [dropped keys]) for a models.ini section on the binary whose
    parsed --help (argspec.parse_help) is `dst`. `src` is the router binary's,
    which wrote the section's names; without it names must match directly."""
    argv, dropped = [], []
    for k, v in settings.items():
        if v is None or str(v).strip() == "":
            continue
        k, v = str(k).strip(), str(v).strip()
        if k in OWNED or k in PRESET_ONLY:
            continue
        spell = {"--" + k, "-" + k}
        s_item = _find(src, spell) or next((i for i in src if i.get("env") == k), None)
        names = spell | set(s_item["flags"]) if s_item else spell
        pos, neg = _polarities(names)
        probe = s_item or _find(dst, names) or _find(dst, pos | neg, want_bool=True)
        if probe is None:
            dropped.append(k)
            continue
        if probe.get("type") != "bool":
            d = _find(dst, names)
            if d is None:
                if not _restates_default(s_item, v):
                    dropped.append(k)
            elif d.get("type") != "bool":
                fit = _fit_value(v, d)
                if fit is None:
                    dropped.append(k)                 # it would refuse to start on it
                else:
                    argv += [_long(d), fit]
            elif _truthy(v):
                argv.append(_long(d))
            continue
        on = _truthy(v) != k.startswith("no-")       # the wanted state of the base option
        want, other = (pos, neg) if on else (neg, pos)
        d = _find(dst, want, want_bool=True)
        if d:
            argv.append(_long(d, want))
        elif not _truthy(v) and s_item and not _find(src, want, want_bool=True):
            continue                                  # the router build skips it too
        elif not _find(dst, other, want_bool=True):
            if not _restates_default(s_item, v, on):
                dropped.append(k)
        # else: the build has only the other polarity, so what's asked is its default
    return argv, dropped


def _has(items, flag):
    return any(flag in (i.get("flags") or ()) for i in items)


def argv_for(server_bin, mid, settings, dst, src=(), api_key=""):
    """([server_bin, -m, path, ...], dropped) for model `mid`; host and port are
    the manager's. ValueError when the section names no model file."""
    path = settings.get("model")
    if not path or not str(path).strip():
        raise ValueError(f"{mid} has no model path in models.ini")
    flags, dropped = translate(settings, dst, src)
    argv = [server_bin, "-m", str(path).strip()]
    if _has(dst, "--alias"):
        argv += ["--alias", mid]
    if _has(dst, "--metrics"):
        argv.append("--metrics")
    if api_key and _has(dst, "--api-key"):
        argv += ["--api-key", api_key]
    return argv + flags, dropped


# ---------------------------------------------------------------- probes

def _popen(argv, log):
    os.makedirs(os.path.dirname(log), exist_ok=True)
    out = logfiles.open_append(log)
    try:
        out.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {subprocess.list2cmdline(argv)}\n")
        out.flush()
        kw = ({"creationflags": _CREATE_NO_WINDOW} if osplat.IS_WIN
              else {"start_new_session": True})     # outlives a panel restart, like the router
        return subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, close_fds=True, **kw)
    finally:
        out.close()                                  # the child holds its own handle


def _health(port):
    """/health's HTTP status, or None when nothing answers."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def _port_free(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return False                             # someone listens there
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


# a crash leaves the log silent, so the exit code has to say what happened.
# Windows reports an NTSTATUS (0xC0000094 arrives as 3221225620), POSIX a signal
_CRASHES = {
    0xC0000005: "access violation",
    0xC0000094: "integer divide by zero",
    0xC000001D: "illegal instruction: built for a CPU feature this one lacks",
    0xC0000135: "a DLL it needs is missing (the CUDA runtime?)",
    0xC0000142: "a DLL failed to initialize",
    0xC0000409: "it aborted (fail-fast)",
    0xC00000FD: "stack overflow",
}
_SIGNALS = {
    4: "illegal instruction: built for a CPU feature this one lacks",
    6: "it aborted",
    8: "arithmetic error",
    9: "killed",
    11: "segmentation fault",
}


def exit_reason(rc):
    """'exit code 0xC0000094: it crashed (integer divide by zero)' and the like."""
    if not isinstance(rc, int):
        return "exit code ?"
    if rc < 0:                                       # Popen: killed by signal -rc
        sig = -rc
        return f"signal {sig}" + (f": it crashed ({_SIGNALS[sig]})" if sig in _SIGNALS else "")
    u = rc & 0xFFFFFFFF
    if u in _CRASHES:
        return f"exit code 0x{u:08X}: it crashed ({_CRASHES[u]})"
    return f"exit code {rc}"


# ---------------------------------------------------------------- manager

class Manager:
    """The process slots this panel started. Every probe is injectable."""

    def __init__(self, logdir, popen=None, health=None, port_free=None, table=None,
                 kill=None, sleep=None):
        self.logdir = logdir
        self._popen = popen or _popen
        self._health = health or _health
        self._port_free = port_free or _port_free
        self._table = table or procs.table
        self._kill = kill or procs.kill
        self._sleep = sleep or time.sleep
        self._lock = threading.RLock()
        self._recs = {}            # mid -> {pid, port, bin, started_at}
        self._p = {}               # mid -> Popen, for the processes this panel spawned

    # ---------- records ----------
    def _path(self):
        return os.path.join(self.logdir, RECORDS)

    def _save(self):
        try:
            os.makedirs(self.logdir, exist_ok=True)
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(self._recs, f)
        except OSError:
            pass

    def reconcile(self):
        """Adopt the recorded processes that are still llama processes (the
        panel restarted under them); drop the rest. server.main() calls it
        once: only the real panel adopts, never a test or preview import."""
        with self._lock:
            try:
                with open(self._path(), encoding="utf-8") as f:
                    recs = json.load(f)
            except (OSError, ValueError):
                recs = {}
            if not isinstance(recs, dict) or not recs:
                return
            table = self._table()
            for mid, r in recs.items():
                if mid in self._recs or not isinstance(r, dict):
                    continue
                if procs.is_llama(table.get(r.get("pid"), (0, ""))[1]):
                    self._recs[mid] = r
            self._save()

    def log_path(self, mid):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", mid).strip("._")[:60] or "model"
        tag = hashlib.sha1(mid.encode("utf-8")).hexdigest()[:6]
        return os.path.join(self.logdir, f"slot-{safe}-{tag}.log")

    def log_tail(self, mid, lines=200):
        return "\n".join(ln.rstrip("\n")
                         for ln in logfiles.tail_lines(self.log_path(mid), lines))

    # ---------- lifecycle ----------
    def _state(self, mid, rec):
        """(state, exit code); state None = the process is gone."""
        p = self._p.get(mid)
        if p is not None:
            rc = p.poll()
            if rc is not None:
                return "failed", rc
            return ("ready" if self._health(rec["port"]) == 200 else "loading"), None
        h = self._health(rec["port"])                 # adopted: no handle to poll
        if h is None:
            return None, None
        return ("ready" if h == 200 else "loading"), None

    def status(self):
        """{mid: {state: loading|ready|failed, port, pid, endpoint, exit_code, bin}}."""
        with self._lock:
            out, gone = {}, []
            for mid, rec in self._recs.items():
                state, rc = self._state(mid, rec)
                if state is None:
                    gone.append(mid)
                    continue
                out[mid] = {"state": state, "port": rec["port"], "pid": rec["pid"],
                            "endpoint": f"http://127.0.0.1:{rec['port']}", "exit_code": rc,
                            "bin": rec.get("bin", ""), "started_at": rec.get("started_at")}
            for mid in gone:
                self._drop(mid)
            if gone:
                self._save()
            return out

    def has(self, mid):
        with self._lock:
            return mid in self._recs

    def _drop(self, mid):
        self._recs.pop(mid, None)
        self._p.pop(mid, None)

    def start(self, mid, argv, port_base=PORT_BASE):
        """(ok, error, port). `argv` is argv_for()'s; host and port are added here.
        A slot that is already up is left alone."""
        with self._lock:
            rec = self._recs.get(mid)
            if rec and self._state(mid, rec)[0] in ("loading", "ready"):
                return True, "", rec["port"]
            self._drop(mid)
            taken = {r.get("port") for r in self._recs.values()}
            port = next((p for p in range(port_base, port_base + PORT_SPAN)
                         if p not in taken and self._port_free(p)), None)
            if port is None:
                return False, (f"no free port in {port_base}-{port_base + PORT_SPAN - 1} "
                               f"for a model process; change slot_port_base"), None
            try:
                p = self._popen(list(argv) + ["--host", "127.0.0.1", "--port", str(port)],
                                self.log_path(mid))
            except OSError as e:
                return False, f"couldn't start {argv[0]}: {e}", None
            self._p[mid] = p
            self._recs[mid] = {"pid": p.pid, "port": port, "bin": argv[0],
                               "started_at": time.time()}
            self._save()
            return True, "", port

    def stop(self, mid, timeout=STOP_TIMEOUT_S):
        """(ok, error). Only this panel's process, by handle or verified PID."""
        with self._lock:
            rec, p = self._recs.get(mid), self._p.get(mid)
        if not rec:
            return True, ""
        ticks = int(timeout / 0.25)
        if p is not None:
            if p.poll() is None:
                p.terminate()
                for _ in range(ticks):
                    if p.poll() is not None:
                        break
                    self._sleep(0.25)
                else:
                    p.kill()
                    p.wait(timeout=5)
        elif procs.is_llama(self._table().get(rec["pid"], (0, ""))[1]):
            self._kill(rec["pid"])
            for _ in range(ticks):
                if self._health(rec["port"]) is None:
                    break
                self._sleep(0.25)
        with self._lock:
            self._drop(mid)
            self._save()
        return True, ""
