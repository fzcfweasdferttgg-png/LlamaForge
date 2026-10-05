"""Run pi once against a loaded model.

pi is Mario Zechner's coding agent (https://github.com/earendil-works/pi, MIT).
LlamaForge drives it, it does not ship it.

Each run is hermetic: a throwaway PI_CODING_AGENT_DIR holding only a models.json
that points at the model's endpoint, so the user's own ~/.pi (logins, extensions,
sessions) never loads. The router key goes in the child's environment only; pi
interpolates "$LLAMAFORGE_API_KEY" from it. The task goes in on stdin: no shell
quoting, no command-line length limit, not visible in the process list.

pi exits 0 even when every request failed, so the outcome is read from its
JSONL events (--mode json), never from the exit code alone.
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

IS_WIN = os.name == "nt"
_NO_WINDOW = 0x08000000
PACKAGE = ("@earendil-works", "pi-coding-agent")
PROVIDER = "llamaforge"
KEY_ENV = "LLAMAFORGE_API_KEY"
MAX_TEXT = 50_000
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600

_READ = ["read", "grep", "find", "ls"]
TOOLSETS = {
    "read": _READ,
    "edit": _READ + ["edit", "write"],
    "full": _READ + ["edit", "write", "bash"],
}
_SHIM_EXTS = ("", ".cmd", ".bat", ".ps1")
_JS_EXTS = (".js", ".mjs", ".cjs")


def _int(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def models_json(mid, endpoint, ctx):
    """pi's models.json for one model behind an OpenAI-compatible endpoint."""
    base = (endpoint or "").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    model = {"id": mid}
    n = _int(ctx)
    if n:
        # pi asks for max_completion_tokens = maxTokens; leave room for the
        # prompt and the files it reads
        model["contextWindow"] = n
        model["maxTokens"] = min(n // 4, 16384)
    return {"providers": {PROVIDER: {"baseUrl": base, "api": "openai-completions",
                                     "apiKey": "$" + KEY_ENV, "models": [model]}}}


def argv(prefix, mid, tools="read"):
    if tools not in TOOLSETS:
        raise ValueError(f"tools must be one of {', '.join(TOOLSETS)}")
    return list(prefix) + [
        "--mode", "json", "--no-session", "--offline",
        "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
        "--no-approve", "--provider", PROVIDER, "--model", mid,
        "--tools", ",".join(TOOLSETS[tools])]


def _pkg_entry(pkg_dir):
    """The package's bin script (bin.pi, or its only bin), or None."""
    try:
        with open(os.path.join(pkg_dir, "package.json"), encoding="utf-8") as f:
            b = json.load(f).get("bin")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(b, dict):
        b = b.get("pi") or (next(iter(b.values())) if len(b) == 1 else None)
    if not isinstance(b, str) or not b:
        return None
    js = os.path.normpath(os.path.join(pkg_dir, b))
    return js if os.path.isfile(js) else None


def _node(which, near, is_win):
    """npm's own shim prefers a node beside it, then PATH."""
    if near:
        cand = os.path.join(near, "node.exe" if is_win else "node")
        if os.path.isfile(cand):
            return cand
    return which("node")


def _with_node(js, which, is_win, near=None):
    node = _node(which, near, is_win) if js else None
    return [node, js] if node else None


def _from_shim(shim, which, is_win):
    # never execute an npm .cmd shim: cmd.exe re-parses its arguments
    d = os.path.dirname(shim)
    js = _pkg_entry(os.path.join(d, "node_modules", *PACKAGE))
    return _with_node(js, which, is_win, near=d)


def locate(pi_bin="", which=shutil.which, is_win=IS_WIN):
    """The command prefix that runs pi, or None when it isn't installed."""
    p = (pi_bin or "").strip()
    if p:
        if os.path.isdir(p):
            return _with_node(_pkg_entry(p), which, is_win)
        if not os.path.isfile(p):
            return None
        ext = os.path.splitext(p)[1].lower()
        if ext in _JS_EXTS:
            return _with_node(p, which, is_win)
        if is_win and ext in _SHIM_EXTS:
            return _from_shim(p, which, is_win)
        return [p]
    found = which("pi")
    if not found:
        return None
    if is_win and os.path.splitext(found)[1].lower() in _SHIM_EXTS:
        return _from_shim(found, which, is_win)
    return [found]


def _text(msg):
    parts = [c.get("text", "") for c in msg.get("content") or []
             if isinstance(c, dict) and c.get("type") == "text"]
    return "".join(p for p in parts if isinstance(p, str)).strip()


def parse_events(lines):
    """The outcome of a run from pi's JSONL events."""
    out = {"text": "", "error": "", "stop": "", "tool_calls": [], "turns": 0,
           "usage": {"input": 0, "output": 0}}
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t == "message_end":
            m = e.get("message")
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            u = m.get("usage") if isinstance(m.get("usage"), dict) else {}
            for k in ("input", "output"):
                out["usage"][k] += _int(u.get(k))
            out["stop"] = str(m.get("stopReason") or "")
            if out["stop"] in ("error", "aborted"):
                out["error"] = str(m.get("errorMessage") or out["stop"])
            else:
                out["error"] = ""
                out["text"] = _text(m)
        elif t == "tool_execution_end":
            out["tool_calls"].append({"tool": str(e.get("toolName") or "?"),
                                      "error": bool(e.get("isError"))})
        elif t == "turn_end":
            out["turns"] += 1
        elif t == "auto_retry_end" and not e.get("success"):
            out["error"] = str(e.get("finalError") or out["error"] or "pi gave up retrying")
    return out


def _kill_tree(proc):
    if IS_WIN:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        # own session: its process group id is its pid
        for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 0)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                proc.wait(timeout=grace or 5)
                break
            except subprocess.TimeoutExpired:
                continue
    try:
        proc.kill()
        proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _say(progress, msg):
    if progress:
        try:
            progress(msg)
        except Exception:  # a broken listener must not break the run
            pass


def run(task, model, endpoint, key="", cwd=None, tools="read", timeout=DEFAULT_TIMEOUT,
        ctx=None, cancel=None, progress=None, cmd=None, pi_bin=""):
    """Run one task to the end. Never raises for run failures: see "ok"/"error"."""
    t0 = time.monotonic()
    cwd = cwd or os.getcwd()
    out = {"ok": False, "text": "", "error": "", "model": model, "endpoint": endpoint,
           "cwd": cwd, "tools": tools, "tool_calls": [], "turns": 0,
           "usage": {"input": 0, "output": 0}, "stop": "", "elapsed_s": 0.0,
           "timed_out": False, "cancelled": False}
    timeout = max(1, min(_int(timeout) or DEFAULT_TIMEOUT, MAX_TIMEOUT))

    if not os.path.isdir(cwd):
        out["error"] = f"cwd is not a directory: {cwd}"
        return out
    cmd = cmd or locate(pi_bin)
    if not cmd:
        out["error"] = ("pi is not installed (npm install -g @earendil-works/pi-coding-agent, "
                        "or set pi_bin in config.json)")
        return out
    try:
        args = argv(cmd, model, tools)
    except ValueError as e:
        out["error"] = str(e)
        return out

    home = tempfile.mkdtemp(prefix="lf-pi-")
    try:
        with open(os.path.join(home, "models.json"), "w", encoding="utf-8") as f:
            json.dump(models_json(model, endpoint, ctx), f, indent=2)
        env = dict(os.environ)
        env["PI_CODING_AGENT_DIR"] = home
        # llama.cpp ignores the header when it has no key; pi wants one either way
        env[KEY_ENV] = key or "no-key"
        kw = ({"creationflags": _NO_WINDOW} if IS_WIN else {"start_new_session": True})
        try:
            proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
        except OSError as e:
            out["error"] = f"could not start pi: {e}"
            return out

        lines, err = [], []

        def feed():
            try:
                proc.stdin.write((task or "").encode("utf-8"))
            except OSError:
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def read_out():
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                lines.append(line)
                if '"tool_execution_start"' in line or '"turn_end"' in line:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("type") == "tool_execution_start":
                        _say(progress, f"pi is using {e.get('toolName') or 'a tool'}")
                    elif e.get("type") == "turn_end":
                        _say(progress, "pi finished a turn")

        def read_err():
            for raw in proc.stderr:
                err.append(raw.decode("utf-8", "replace"))
                del err[:-50]

        threads = [threading.Thread(target=f, daemon=True) for f in (feed, read_out, read_err)]
        for th in threads:
            th.start()
        while True:
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                out["cancelled"] = True
                _kill_tree(proc)
                break
            if time.monotonic() - t0 > timeout:
                out["timed_out"] = True
                _kill_tree(proc)
                break
        for th in threads:
            th.join(timeout=5)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass

        ev = parse_events(lines)
        for k in ("text", "tool_calls", "turns", "usage", "stop"):
            out[k] = ev[k]
        tail = "".join(err).strip()[-1500:]
        if out["cancelled"]:
            out["error"] = "cancelled"
        elif out["timed_out"]:
            out["error"] = f"pi hit the {timeout}s timeout"
        elif proc.returncode:
            why = ev["error"] or tail
            out["error"] = f"pi exited with code {proc.returncode}" + (f": {why}" if why else "")
        elif ev["error"]:
            out["error"] = ev["error"]
        elif not ev["stop"]:
            out["error"] = "pi produced no answer" + (f": {tail}" if tail else "")
        out["ok"] = not out["error"]
        if len(out["text"]) > MAX_TEXT:
            out["text"] = out["text"][:MAX_TEXT] + "\n...[truncated]"
        return out
    finally:
        out["elapsed_s"] = round(time.monotonic() - t0, 1)
        shutil.rmtree(home, ignore_errors=True)
