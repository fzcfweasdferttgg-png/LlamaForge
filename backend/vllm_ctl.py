"""vLLM process manager — the multi-model router that vLLM itself lacks.

One `vllm serve` process serves one model. This manager owns a *list* of
instances (v1 caps at one via a guard; lifting it + per-instance ports enables
concurrent serving later). It spawns inside WSL via wsl.popen with stdout/stderr
redirected to Windows-side log files (same pattern as router_ctl.py), polls
vLLM's /health over WSL localhost-forwarding, and reconciles live processes on
startup so restarting LlamaForge never loses or double-starts a model.

Pure stdlib.
"""
import os, re, time, threading, urllib.request

import logfiles, wsl

MAX_INSTANCES = 1                     # v1 guard; raise for concurrency
READY_TIMEOUT = 600                   # seconds before a stuck load -> failed
HEALTH_INTERVAL = 3


def settings_to_flags(settings):
    """{knob: value} -> ["--knob", "value", ...] with store-true booleans handled.
    'true' -> bare flag; 'false' -> omitted; everything else -> --k v.

    Returns a *list*, not a shell string: these keys and values come straight
    from the /api/vllm/save request body, and joining them into a command line
    let a value like `1 --foo` (or worse, `1; rm -rf ~`) inject arguments and
    shell syntax. As a list each element stays one argv slot."""
    parts = []
    for k, v in settings.items():
        k = str(k).strip()
        v = str(v).strip()
        if not k or not _KNOB_RE.match(k):
            continue                    # ignore junk keys rather than pass them on
        if v.lower() == "true":
            parts.append(f"--{k}")
        elif v.lower() == "false" or v == "":
            continue
        else:
            parts.extend([f"--{k}", v])
    return parts


# vLLM long options: letters/digits/dash/underscore/dot only.
_KNOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_serve_script(venv):
    """The bash script run inside WSL to serve one model.

    Only the venv (config-supplied) is embedded; the model ref, port and every
    knob arrive as positional parameters, so none of them can be parsed as
    shell syntax. "${@:3}" forwards the flag list verbatim."""
    return (f'exec {wsl.sh_path(venv)}/bin/vllm serve "$1" '
            f'--host 0.0.0.0 --port "$2" "${{@:3}}"')


class Manager:
    def __init__(self, distro, port, venv, logdir):
        self.distro = distro
        self.port = port
        self.venv = venv
        self.logdir = logdir
        self.instances = []           # [{model_id, port, state, started_at}]
        self.lock = threading.Lock()

    # ---------- lifecycle ----------
    def start(self, model_id, model_ref, flags):
        """`flags` is the list from settings_to_flags()."""
        with self.lock:
            if len(self.instances) >= MAX_INSTANCES:
                return False, "a vLLM model is already running (stop it first)"
            os.makedirs(self.logdir, exist_ok=True)
            out = logfiles.open_append(os.path.join(self.logdir, "vllm.out.log"))
            err = logfiles.open_append(os.path.join(self.logdir, "vllm.err.log"))
            wsl.popen(build_serve_script(self.venv),
                      model_ref, self.port, *(flags or []),
                      stdout=out, stderr=err, distro=self.distro)
            self.instances.append({"model_id": model_id, "port": self.port,
                                   "state": "starting", "started_at": time.time()})
        threading.Thread(target=self._await_ready, args=(model_id,), daemon=True).start()
        return True, ""

    def _await_ready(self, model_id):
        # We sleep before the first health check so the instance provably stays
        # in "starting" for one interval after start() returns — the caller (and
        # tests) can observe "starting" without racing this daemon thread. vLLM
        # takes 1-5 min to come up, so a HEALTH_INTERVAL head start is free.
        # The UI maps both "starting" and "loading" to "loading" anyway.
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            time.sleep(HEALTH_INTERVAL)
            if self._health_ok():
                self._set_state(model_id, "ready")
                return
            self._set_state(model_id, "loading")
        self._set_state(model_id, "failed")

    def _health_ok(self):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def stop(self, model_id):
        wsl.run("pkill -f 'vllm serve' || true", distro=self.distro, timeout=20)
        deadline = time.time() + 15
        while time.time() < deadline and self._health_ok():
            time.sleep(0.5)
        with self.lock:
            self.instances = [i for i in self.instances if i["model_id"] != model_id]
        return True

    # ---------- state ----------
    def _set_state(self, model_id, state):
        with self.lock:
            for i in self.instances:
                if i["model_id"] == model_id:
                    i["state"] = state

    def status(self):
        with self.lock:
            return [{"model_id": i["model_id"], "port": i["port"],
                     "state": i["state"], "started_at": i["started_at"],
                     "endpoint": f"http://127.0.0.1:{i['port']}"}
                    for i in self.instances]

    def reconcile(self):
        """On startup: if we think something's running but no vllm process
        exists in WSL, drop the stale instance record."""
        if not self.instances:
            return
        code, _out, _err = wsl.run("pgrep -f 'vllm serve'", distro=self.distro, timeout=15)
        if code != 0:                 # pgrep exit 1 == no match
            with self.lock:
                self.instances = []
