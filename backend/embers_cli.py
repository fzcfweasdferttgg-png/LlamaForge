"""Command-line entry point for Embers (M1 has no UI yet).

  python backend/embers_cli.py create morning --template templates/morning-brief.json --bind notes=D:/Notes
  python backend/embers_cli.py run morning        # ingest, then brief
  python backend/embers_cli.py ingest|brief|lint morning [--model ID]
  python backend/embers_cli.py list               # embers and the last run of each job
  python backend/embers_cli.py tick               # one scheduler pass: what the panel would run now

It uses the router LlamaForge already runs (config router_port and
router_api_key) and whatever model is loaded, unless --model is given. The
job commands never load or unload models; `tick` follows the panel's
scheduler rules (embers/scheduler.py), but a fresh process has seen no idle
time yet, so it never swaps models.

Exit status: 0 when every step ended ok or partial; 1 when a step failed or
was aborted, the router is down, or an error stopped the command. Errors print
one clean line; set EMBERS_DEBUG=1 to get the traceback instead.

Everything printed (model ids, run errors, router replies) is stripped of
control characters and terminal escape sequences first: it can come from a
model, a source file or the router.

Lives in backend/ rather than as `python -m embers` because from the repo root
`embers` would resolve to the data folder <ROOT>/embers, not this package.
"""
import argparse, contextlib, datetime as dt, os, re, sys

import config
import embers
from embers import jobs, lock, scheduler, templates, wikifs
from embers.llm import MAX_N_CTX, LLMError, Router, RouterUnavailable, clamp_n_ctx  # noqa: F401 (MAX_N_CTX re-exported)

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
            r = e.store.last_run(job, ("ok", "partial", "failed", "aborted", "running", "skipped"))
            if r:
                found.append(f"{job} {r['status']} ({str(r['started'] or '')[:16]})")
    return ", ".join(found)


def _list(base, out):
    for name, root in scheduler.list_embers(base):
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
    with contextlib.ExitStack() as stack:
        try:                               # the panel's scheduler may be running this ember
            stack.enter_context(lock.held(root))
        except lock.Busy as e:             # only the acquire maps to this message
            pid = e.pid if e.pid is not None else "unknown"
            raise _Fail(f"{a.id} is already running in another process (pid {pid}; lock: {e.path})")
        return _run_steps(a, root, cfg, router_cls, out)


def _run_steps(a, root, cfg, router_cls, out):
    with jobs.Ember(root) as ember:
        router = router_cls(cfg)
        pinned = ember.conf.get("model")
        model = a.model or (pinned if isinstance(pinned, str) else "") or router.loaded_model()
        if not model or not isinstance(model, str):
            raise SystemExit("No model is loaded in the router. Load one in LlamaForge or pass --model.")
        n_ctx, llm = clamp_n_ctx(router.n_ctx(model)), router.llm(model)
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


def _tick(a, router_cls, out, err):
    """One scheduler pass in the foreground: what the panel would do right now."""
    s = scheduler.Scheduler(config.load, router_cls=router_cls, now=lambda: a.now,
                            runners=scheduler.RUNNERS)
    r = s.tick()
    if r["ran"]:
        eid, job, status = r["ran"]
        _say(out, f"ran {eid} {job}: {status}")
    for eid, waits in sorted(r["waiting"].items()):
        for job, reason in waits.items():
            _say(out, f"{eid} {job}: waiting: {reason}")
    for eid, job, reason in r["skipped"]:
        _say(out, f"{eid} {job}: skipped: {reason}")
    if not (r["ran"] or r["waiting"] or r["skipped"] or r["error"]):
        _say(out, "nothing due")
        st = s.status()
        for eid, info in sorted((k, v) for k, v in st.items() if k != "last_error"):
            if not info.get("enabled"):
                _say(out, f"{eid}  disabled")
                continue
            nxt = ", ".join(f"{job} {when}" for job, when in info.get("next", {}).items())
            _say(out, f"{eid}  next: {nxt}" if nxt else f"{eid}  no scheduled jobs")
    if r["error"]:
        _say(err, f"error: {r['error']}")
        return 1
    return 0


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
    sub.add_parser("tick", help="one scheduler pass now: run, wait or skip what is due")
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
        if a.cmd == "tick":
            return _tick(a, router_cls, out, err)
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
