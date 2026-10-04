"""Command-line entry point for Embers (M1 has no UI yet).

  python backend/embers_cli.py create morning --template templates/morning-brief.json --bind notes=D:/Notes
  python backend/embers_cli.py run morning        # ingest, then brief
  python backend/embers_cli.py ingest|brief|lint morning [--model ID]
  python backend/embers_cli.py list               # embers and the last run of each job

It uses the router LlamaForge already runs (config router_port and
router_api_key) and whatever model is loaded, unless --model is given. It
never loads or unloads models itself (that is M2's router gating).

Exit status: 0 when every step ended ok or partial; 1 when a step failed or
was aborted, the router is down, or an error stopped the command. Errors print
one clean line; set EMBERS_DEBUG=1 to get the traceback instead.

Everything printed (model ids, run errors, router replies) is stripped of
control characters and terminal escape sequences first: it can come from a
model, a source file or the router.

Lives in backend/ rather than as `python -m embers` because from the repo root
`embers` would resolve to the data folder <ROOT>/embers, not this package.
"""
import argparse, datetime as dt, os, re, sys

import config
import embers
from embers import jobs, lock, templates, wikifs
from embers.llm import DEFAULT_N_CTX, LLMError, Router, RouterUnavailable

MAX_N_CTX = 1 << 20          # a larger "context" from /props is not believable; clamp it
REASON_CHARS = 300
JOBS = ("ingest", "brief", "lint")
ROUTER_DOWN = ("The router is not reachable (down, restarting or loading a model). "
               "Please start the router in LlamaForge, then try again.")

MAX_BIND = 4096

_ANSI, _CTRL = wikifs.ANSI, wikifs.CTRL      # one definition, shared with the wiki writer
_BREAKS = "\t\n\r\x85\u2028\u2029"


def safe(text):
    """One printable line: escape sequences removed, other control chars dropped
    (tabs and line breaks become spaces)."""
    s = _ANSI.sub("", str(text))
    return _CTRL.sub(lambda m: " " if m.group() in _BREAKS else "", s).strip()


class _Fail(Exception):
    """A clean, expected stop: message for stderr, exit status 1."""


def _say(stream, text, prefix=""):
    """Print one sanitised line. Characters the stream cannot encode (a redirected
    stdout on Windows is cp1252/strict) become backslash escapes instead of raising."""
    line = prefix + safe(text)
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        line = line.encode(enc, "backslashreplace").decode(enc)
    except LookupError:
        line = line.encode("ascii", "backslashreplace").decode("ascii")
    print(line, file=stream)


def _debug():
    return os.environ.get("EMBERS_DEBUG") == "1"


def _parse_binds(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(safe(f"--bind expects slot=value, got {p!r}"))
        k, v = p.split("=", 1)
        if _CTRL.search(p):
            raise SystemExit(safe(f"--bind must not contain control or invisible characters: {p!r}"[:300]))
        if len(v.strip()) > MAX_BIND:
            raise SystemExit(f"--bind value for {safe(k)[:40]!r} is longer than {MAX_BIND} characters")
        out[k.strip()] = v.strip()
    return out


def _ember_root(base, name):
    """The ember's folder, or SystemExit: a valid id that resolves under base."""
    if not isinstance(name, str) or not jobs.ID_RE.fullmatch(name) or embers.reserved_name(name):
        raise SystemExit(safe(f"invalid ember name {name!r}: use lowercase letters, digits and "
                              "dashes (not a Windows device name)"))
    root = os.path.join(base, name)
    real_base, real_root = os.path.realpath(base), os.path.realpath(root)
    try:
        inside = os.path.commonpath([real_base, real_root]) == real_base and real_root != real_base
    except ValueError:                     # different drives
        inside = False
    if not inside:
        raise SystemExit(safe(f"ember {name!r} resolves outside {base}"))
    return root


def _n_ctx(value):
    """The router's context size as an int in [1, MAX_N_CTX], else DEFAULT_N_CTX.
    Values below jobs.MIN_N_CTX pass through: the jobs record why they cannot run."""
    if isinstance(value, bool):
        return DEFAULT_N_CTX
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or value <= 0:
        return DEFAULT_N_CTX
    return min(value, MAX_N_CTX)


def _run_error(ember, run):
    try:
        row = ember.store.db.execute("SELECT error FROM runs WHERE id=?", (run,)).fetchone()
    except Exception:
        return ""
    return (row[0] if row else "") or ""


def _last_runs(root):
    with jobs.Ember(root) as e:
        found = []
        for job in JOBS:
            r = e.store.last_run(job, ("ok", "partial", "failed", "aborted", "running"))
            if r:
                found.append(f"{job} {r['status']} ({str(r['started'] or '')[:16]})")
    return ", ".join(found)


def _list(base, out):
    if not os.path.isdir(base):
        return 0
    for name in sorted(os.listdir(base)):
        root = os.path.join(base, name)
        if not (jobs.ID_RE.fullmatch(name) and not embers.reserved_name(name)
                and os.path.isfile(os.path.join(root, "ember.json"))):
            continue
        try:
            last = _last_runs(root)
        except Exception as e:             # one broken ember must not hide the others
            _say(out, f"{name}  (unreadable: {type(e).__name__}: {e})"[:400])
            continue
        _say(out, f"{name}  last: {last}" if last else name)
    return 0


def _create(a, base, out):
    binds = _parse_binds(a.bind)
    _ember_root(base, a.id)                # refuse bad names and escapes before reading anything
    with open(a.template, "rb") as f:
        tpl = templates.parse_template(f.read(templates.MAX_BYTES + 1))
    root = jobs.create_ember(base, tpl, a.id, binds, a.now)
    _say(out, f"Created {a.id} at {root}")
    if tpl["dropped"]:
        _say(out, "Ignored template keys: " + ", ".join(tpl["dropped"]))
    return 0


def _jobs(a, base, cfg, router_cls, out):
    root = _ember_root(base, a.id)
    if not os.path.isfile(os.path.join(root, "ember.json")):
        raise SystemExit(safe(f"No ember named {a.id!r} in {base}"))
    try:
        with lock.held(root):              # the panel's scheduler may be running this ember
            return _run_steps(a, root, cfg, router_cls, out)
    except lock.Busy as e:
        pid = e.pid if e.pid is not None else "unknown"
        raise _Fail(f"{a.id} is already running in another process (pid {pid})")


def _run_steps(a, root, cfg, router_cls, out):
    with jobs.Ember(root) as ember:
        router = router_cls(cfg)
        pinned = ember.conf.get("model")
        model = a.model or (pinned if isinstance(pinned, str) else "") or router.loaded_model()
        if not model or not isinstance(model, str):
            raise SystemExit("No model is loaded in the router. Load one in LlamaForge or pass --model.")
        n_ctx, llm = _n_ctx(router.n_ctx(model)), router.llm(model)
        _say(out, f"Using {model} (context {n_ctx})")
        code = 0
        for step in (["ingest", "brief"] if a.cmd == "run" else [a.cmd]):
            r = getattr(jobs, step)(ember, llm, a.now, n_ctx=n_ctx)
            status = r.get("status")
            _say(out, f"{step}: {status}: {r.get('summary', '')}")
            if step == "brief" and r.get("path"):
                _say(out, f"Brief written to {r['path']}")
            error = _run_error(ember, r.get("run")) if status != "ok" else ""
            if error:
                _say(out, error[:REASON_CHARS], prefix="  reason: ")
            if status not in ("ok", "partial"):    # fail closed on anything unexpected
                code = 1
            if r.get("router_down") is True:       # the job's own flag, never the error text
                raise _Fail(ROUTER_DOWN)
        return code


def main(argv=None, now=None, router_cls=Router, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    ap = argparse.ArgumentParser(prog="embers_cli", description="Run LlamaForge embers from the command line.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create an ember from a template")
    c.add_argument("id")
    c.add_argument("--template", required=True)
    c.add_argument("--bind", action="append", help="slot=path_or_url (repeatable)")
    for name in JOBS + ("run",):
        s = sub.add_parser(name, help="run now" if name != "run" else "ingest, then brief")
        s.add_argument("id")
        s.add_argument("--model", default="")
    sub.add_parser("list", help="list embers and the last run of each job")
    a = ap.parse_args(argv)
    a.now = now or dt.datetime.now()

    cfg = config.load()
    if config.LOAD_ERROR:
        _say(err, f"warning: {config.LOAD_ERROR}")
    base = embers.embers_dir(cfg)
    try:
        if a.cmd == "list":
            return _list(base, out)
        if a.cmd == "create":
            return _create(a, base, out)
        return _jobs(a, base, cfg, router_cls, out)
    except _Fail as e:
        _say(err, str(e))
        return 1
    except RouterUnavailable as e:
        if _debug():
            raise
        _say(err, f"error: {e}")
        _say(err, ROUTER_DOWN)
        return 1
    except LLMError as e:
        if _debug():
            raise
        _say(err, f"error: {e}")
        return 1
    except Exception as e:                 # jobs close their run row before re-raising
        if _debug():
            raise
        _say(err, f"error: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):  # covers SystemExit messages printed by Python too
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(errors="backslashreplace")
    try:
        sys.exit(main())
    except KeyboardInterrupt:              # the job already closed its run row
        print("interrupted", file=sys.stderr)
        sys.exit(130)
