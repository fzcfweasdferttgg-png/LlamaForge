"""HTTP handlers behind the panel's Embers tab.

Each handler takes (req, cfg) and returns (status, payload); routes.py wraps
them and turns Error into its ApiError. Nothing here runs a job itself: "run
now" queues on the scheduler, which takes the ember's lock like any
scheduled run. Ask is the one model call made on the request thread, and it
only ever uses a model that is already loaded (autoload=False).

Deleting an ember renames ember.json to ember.removed.json and keeps the
wiki: the scheduler stops seeing it, and nothing the user wrote is lost.
"""
import datetime as dt, json, os, re, tempfile, threading

import atomicio, config
from embers import embers_dir, forge, jobs, lock, push, reserved_name, templates, view, wikifs
from embers.ask import AskError, ask
from embers.llm import LLMError, Router, RouterUnavailable, clamp_n_ctx
from embers.scheduler import jobs_conf, list_embers

SCHEDULER     = None                 # server.main() sets the live Scheduler
ROUTER_CLS    = Router
MAIN_FN       = lambda: ""           # routes.py: the pool's main model, listed first
NOW_FN        = dt.datetime.now
PUSH_OPENER   = push._open
SAVE_FN       = config.update      # persists the embers folder choice
TEMPLATES_DIR = os.path.join(config.ROOT, "templates")
REMOVED       = "ember.removed.json"
RUN_SETS      = {"run": ["ingest", "brief"], "ingest": ["ingest"], "brief": ["brief"], "lint": ["lint"]}
UPDATABLE     = ("id", "name", "bindings", "jobs", "model", "enabled", "push")
MAX_NAME      = 120
MAX_MODEL     = 200
MAX_RAW_CHARS = 200_000
MAX_RAWS      = 300
MAX_RUNS      = 30
LOG_LINES     = 60
ERROR_CHARS   = 300
FULL_PATH     = r"use a full path, like D:\Notes\Embers or ~/notes/embers"
_DAY_RE       = re.compile(r"\d{4}-\d{2}-\d{2}")
_CTRL         = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_conf_lock    = threading.Lock()     # ember.json read-modify-write (create/update/delete)


class Error(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _msg(e):
    return f"{type(e).__name__}: {e}"[:ERROR_CHARS]


def _valid_id(eid):
    if not isinstance(eid, str) or not jobs.ID_RE.fullmatch(eid) or reserved_name(eid):
        raise Error(400, "ember ids are lowercase letters, digits and dashes")
    return eid


def _root(cfg, eid):
    root = os.path.join(embers_dir(cfg), _valid_id(eid))
    if not os.path.isfile(os.path.join(root, "ember.json")):
        raise Error(404, f"no ember named {eid!r}")
    return root


def _brief_dates(root):
    """Days with a brief, newest first."""
    folder = os.path.join(root, "briefs")
    if not os.path.isdir(folder):
        return []
    return sorted((n[:-3] for n in os.listdir(folder)
                   if n.endswith(".md") and _DAY_RE.fullmatch(n[:-3])), reverse=True)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ reading

def _card(eid, root, sched):
    with jobs.Ember(root) as e:
        conf, st, tpl = e.conf, e.store, e.template
        last = {}
        for job in templates.JOBS:
            r = st.last_attempt(job)
            if r:
                last[job] = {"status": r["status"], "started": r["started"], "finished": r["finished"],
                             "error": (r["error"] or "")[:ERROR_CHARS]}
        counts = {"pages": len(st.all_pages()), "open": len(st.open_items()),
                  "verified": len(st.open_items(verified_only=True))}
    dates, brief = _brief_dates(root), None
    if dates:
        parsed = push.parse_brief(os.path.join(root, "briefs", dates[0] + ".md"))
        brief = {"date": dates[0], "headline": parsed[0] if parsed else "", "count": len(dates)}
    try:
        push_conf = push.validate(conf.get("push"))
    except ValueError:
        push_conf = None
    return {
        "id": eid, "name": conf.get("name") or eid, "template": tpl["name"], "title": tpl["title"],
        "mission": tpl["mission"], "model_hint": tpl["model_hint"], "created": conf.get("created"),
        "origin": conf.get("origin") or "",
        "enabled": conf.get("enabled", True) is not False, "model": conf.get("model") or "",
        "bindings": conf.get("bindings") or {}, "slots": tpl["slots"], "schedule": jobs_conf(conf),
        "next": sched.get("next") or {}, "running": sched.get("running"),
        "waiting": sched.get("waiting") or {}, "queued": sched.get("queued") or [],
        "last": last, "brief": brief, "counts": counts,
        "push": push_conf, "push_last": push.last(root), "root": root}


def get_embers(req, cfg):
    status = SCHEDULER.status() if SCHEDULER else {}
    cards = []
    for eid, root in list_embers(embers_dir(cfg)):
        try:
            cards.append(_card(eid, root, status.get(eid) or {}))
        except Exception as e:             # one broken ember.json must not hide the rest
            cards.append({"id": eid, "name": eid, "root": root, "error": _msg(e)})
    return 200, {"embers": cards, "embers_dir": embers_dir(cfg), "scheduler": SCHEDULER is not None,
                 "scheduled": cfg.get("embers_scheduler") is not False,
                 "last_error": status.get("last_error")}


def _template(name):
    if not isinstance(name, str) or not templates.NAME_RE.fullmatch(name) or reserved_name(name):
        raise Error(404, "no such template")
    path = os.path.join(TEMPLATES_DIR, name + ".json")
    try:
        with open(path, "rb") as f:
            return templates.parse_template(f.read(templates.MAX_BYTES + 1))
    except FileNotFoundError:
        raise Error(404, f"no template named {name!r}") from None
    except (OSError, ValueError) as e:
        raise Error(500, f"template {name!r} is broken: {e}") from None


def _zero_setup(tpl):
    """Runs without the user pointing it at anything."""
    auto = [s for s in tpl["slots"] if s["type"] in templates.AUTO_SLOTS]
    return bool(auto) and not any(s["required"] for s in tpl["slots"] if s not in auto)


def get_templates(req, cfg):
    out = []
    names = sorted(os.listdir(TEMPLATES_DIR)) if os.path.isdir(TEMPLATES_DIR) else []
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            t = _template(n[:-5])
        except Error:
            continue
        out.append({k: t[k] for k in ("name", "title", "mission", "about", "model_hint",
                                       "page_kinds", "slots", "jobs")} | {"zero_setup": _zero_setup(t)})
    return 200, {"templates": out}


def get_brief(req, cfg):
    root = _root(cfg, req.q("id"))
    dates, date = _brief_dates(root), req.q("date", "")
    if date:
        if not isinstance(date, str) or not _DAY_RE.fullmatch(date):
            raise Error(400, "dates look like 2026-10-05")
        if date not in dates:
            raise Error(404, f"no brief for {date}")
    elif not dates:
        return 200, {"date": None, "dates": [], "html": ""}
    else:
        date = dates[0]
    return 200, {"date": date, "dates": dates,
                 "html": view.render(_read(os.path.join(root, "briefs", date + ".md")))}


def get_pages(req, cfg):
    root = _root(cfg, req.q("id"))
    with jobs.Ember(root) as e:
        pages = [{k: p.get(k) for k in ("page", "title", "summary", "updated", "open")}
                 for p in e.store.all_pages()]
        raws = [{k: r.get(k) for k in ("sha", "source", "title", "fetched", "size", "status")}
                for r in reversed(e.store.all_raws()[-MAX_RAWS:])]
    index = os.path.join(root, "index.md")
    return 200, {"pages": pages, "raws": raws,
                 "index_html": view.render(_read(index)) if os.path.isfile(index) else ""}


def get_page(req, cfg):
    root = _root(cfg, req.q("id"))
    page = req.q("page")
    try:
        path = wikifs.page_path(root, page)
    except wikifs.WikiError:
        raise Error(400, "page names look like projects/acme") from None
    if not os.path.isfile(path):
        raise Error(404, f"no page {page!r}")
    with jobs.Ember(root) as e:
        row = e.store.get_page(page) or {}
    return 200, {"page": page, "title": row.get("title") or page, "html": view.render(_read(path))}


def get_raw(req, cfg):
    root = _root(cfg, req.q("id"))
    sha = req.q("sha")
    try:
        text = wikifs.read_raw(root, sha)
    except wikifs.WikiError:
        raise Error(400, "raw ids are 12 hex digits") from None
    if text is None:
        raise Error(404, "that source snapshot is gone (pruned, or never fetched)")
    with jobs.Ember(root) as e:
        meta = next((r for r in e.store.all_raws() if r["sha"] == sha), {})
    return 200, {"sha": sha, "text": text[:MAX_RAW_CHARS], "truncated": len(text) > MAX_RAW_CHARS,
                 **{k: meta.get(k) for k in ("source", "ref", "title", "fetched", "status")}}


def get_log(req, cfg):
    root = _root(cfg, req.q("id"))
    with jobs.Ember(root) as e:
        runs = [{k: r.get(k) for k in ("id", "job", "status", "started", "finished", "tokens_in", "tokens_out")}
                | {"error": (r.get("error") or "")[:ERROR_CHARS]} for r in e.store.recent_runs(MAX_RUNS)]
    path, log = os.path.join(root, "log.md"), []
    if os.path.isfile(path):
        log = [ln for ln in _read(path).splitlines() if ln.startswith("## [")][-LOG_LINES:]
    return 200, {"runs": runs, "log": log}


# ------------------------------------------------------------------ changing

def _need_source(tpl, clean):
    if not clean and not any(s["type"] in templates.AUTO_SLOTS for s in tpl["slots"]):
        raise Error(400, "point this ember at at least one source (a folder, calendar, repo or feed)")


def _bindings(tpl, raw):
    try:
        clean = jobs.clean_bindings(tpl, {} if raw is None else raw)
    except ValueError as e:
        raise Error(400, str(e)) from None
    _need_source(tpl, clean)
    return clean


def _free_id(base, name):
    """(id, n): the template's name, or name-2, name-3... when taken."""
    stem = name[:60]
    for n in range(1, 100):
        eid = stem if n == 1 else f"{stem}-{n}"
        if not os.path.exists(os.path.join(base, eid)):
            return eid, n
    raise Error(409, "too many embers from this template; name the new one yourself")


def post_create(req, cfg):
    b = req.body
    tpl = _template(b.get("template"))
    clean = _bindings(tpl, b.get("bindings"))
    base = embers_dir(cfg)
    with _conf_lock:
        eid, name = b.get("id"), None
        if eid in (None, ""):
            eid, n = _free_id(base, tpl["name"])
            name = f"{tpl['title']} {n}" if n > 1 else None
        root = os.path.join(base, _valid_id(eid))
        if os.path.exists(root):
            if os.path.isfile(os.path.join(root, REMOVED)):
                raise Error(409, f"an ember named {eid!r} was removed and its wiki is still in {root}; "
                                 "pick another name, or delete that folder first")
            raise Error(409, f"{root} already exists; pick another name")
        try:
            root = jobs.create_ember(base, tpl, eid, clean, NOW_FN(), name=name)
        except ValueError as e:
            raise Error(400, str(e)) from None
    queued = []
    if b.get("start", True) is not False and SCHEDULER:
        queued = SCHEDULER.request(eid, RUN_SETS["run"])
    return 200, {"id": eid, "root": root, "queued": queued}


def _name(v):
    if not isinstance(v, str):
        raise Error(400, "name must be text")
    v = " ".join(v.split())
    if not v or len(v) > MAX_NAME:
        raise Error(400, f"name must be 1-{MAX_NAME} characters")
    return v


def _model(v):
    if not isinstance(v, str) or len(v) > MAX_MODEL or _CTRL.search(v):
        raise Error(400, "model must be a model id, or empty for whichever model is loaded")
    return v.strip()


def post_update(req, cfg):
    b = req.body
    root = _root(cfg, b.get("id"))
    unknown = sorted(set(b) - set(UPDATABLE))
    if unknown:
        raise Error(400, f"can't change: {', '.join(map(str, unknown))[:200]}")
    path = os.path.join(root, "ember.json")
    with _conf_lock:
        with open(path, encoding="utf-8") as f:
            conf = json.load(f)
        tpl = templates.parse_template(conf["template"])
        if "name" in b:
            conf["name"] = _name(b["name"])
        if "enabled" in b:
            if not isinstance(b["enabled"], bool):
                raise Error(400, "enabled must be true or false")
            conf["enabled"] = b["enabled"]
        if "model" in b:
            conf["model"] = _model(b["model"])
        if "bindings" in b:
            conf["bindings"] = _bindings(tpl, b["bindings"])
        if "jobs" in b:
            if not isinstance(b["jobs"], dict):
                raise Error(400, "jobs must be an object like {\"brief\": \"07:00\"}")
            dropped = []
            try:
                merged = templates._jobs(dict(jobs_conf(conf), **b["jobs"]), dropped)
            except ValueError as e:
                raise Error(400, str(e)) from None
            if dropped:
                raise Error(400, f"unknown job(s): {', '.join(dropped)[:200]}")
            conf["jobs"] = merged
        if "push" in b:
            try:
                p = push.validate(b["push"])
            except ValueError as e:
                raise Error(400, str(e)) from None
            if p:
                conf["push"] = p
            else:
                conf.pop("push", None)
        atomicio.write_json(path, conf)
    return 200, {"ok": True, "id": conf["id"]}


def post_delete(req, cfg):
    eid = req.body.get("id")
    root = _root(cfg, eid)
    with _conf_lock:
        try:
            with lock.held(root):
                os.replace(os.path.join(root, "ember.json"), os.path.join(root, REMOVED))
        except lock.Busy:
            raise Error(409, "this ember is running a job; remove it when the job finishes") from None
    if SCHEDULER:
        SCHEDULER.cancel(eid)
    return 200, {"ok": True, "kept": root}


def post_run(req, cfg):
    eid = req.body.get("id")
    _root(cfg, eid)
    job = req.body.get("job", "run")
    if job not in RUN_SETS:
        raise Error(400, f"job must be one of {', '.join(RUN_SETS)}")
    if not SCHEDULER:
        raise Error(503, "the embers scheduler isn't running in this panel (it starts with LlamaForge)")
    try:
        return 200, {"queued": SCHEDULER.request(eid, RUN_SETS[job])}
    except ValueError as e:
        raise Error(400, str(e)) from None


def post_cancel(req, cfg):
    eid = req.body.get("id")
    _root(cfg, eid)
    return 200, {"dropped": SCHEDULER.cancel(eid) if SCHEDULER else []}


def post_ask(req, cfg):
    root = _root(cfg, req.body.get("id"))
    question = req.body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise Error(400, "ask a question")
    router = ROUTER_CLS(cfg)
    with jobs.Ember(root) as e:
        try:
            loaded = router.loaded_ids(MAIN_FN())
        except LLMError as ex:
            raise Error(503, f"the llama.cpp router isn't answering ({_msg(ex)})") from None
        pinned = e.conf.get("model") or ""
        model = pinned if pinned in loaded else (loaded[0] if loaded else None)
        if not model:
            raise Error(409, "Load a model in LlamaForge first. Ask uses a model that is already "
                             "loaded and never loads one itself.")
        try:
            result = ask(e, router.llm(model, autoload=False), question, NOW_FN(),
                         clamp_n_ctx(router.n_ctx(model)))
        except RouterUnavailable as ex:
            raise Error(503, _msg(ex)) from None
        except LLMError as ex:
            raise Error(502, f"{model} gave an answer that couldn't be used ({_msg(ex)})") from None
        except AskError as ex:
            raise Error(409, str(ex)) from None
        except ValueError as ex:
            raise Error(400, str(ex)) from None
    return 200, dict(result, model=model)


FORGE_MESSAGES = 100        # messages in one conversation
FORGE_BUILDS   = 3          # embers one conversation may build


def _forge_body(b):
    msgs = b.get("messages")
    if not isinstance(msgs, list) or not msgs or len(msgs) > FORGE_MESSAGES:
        raise Error(400, f"a Forge conversation holds 1-{FORGE_MESSAGES} messages; start over")
    for m in msgs:
        if not isinstance(m, dict) or not isinstance(m.get("text"), str) or len(m["text"]) > forge.MAX_TEXT:
            raise Error(400, f"each message is text of at most {forge.MAX_TEXT} characters")
        if m.get("notes") is not None and not isinstance(m["notes"], list):
            raise Error(400, "notes must be a list")
    built = b.get("built") or []
    if not isinstance(built, list) or not all(isinstance(x, str) for x in built):
        raise Error(400, "built must be a list of ember ids")
    return msgs, built[:20]


def _forge_build(cfg, out, model, built):
    """Build the drafted ember. Returns (built or None, notes)."""
    if len(built) >= FORGE_BUILDS:
        return None, [f"Not built: Forge builds at most {FORGE_BUILDS} embers per conversation. "
                      "Start over to build another."]
    try:
        tpl, bindings = forge.assemble(out["draft"], model, NOW_FN())
    except ValueError as e:
        return None, [f"Not built: {e}."]
    name = tpl["name"]
    done = next((x for x in built if x == name or re.fullmatch(re.escape(name) + r"-\d+", x)), None)
    if done:
        return None, [f"Not built: this conversation already built {done}. Change the title to build a new one."]
    base = embers_dir(cfg)
    with _conf_lock:
        eid, n = _free_id(base, name)
        title = f"{tpl['title']} {n}" if n > 1 else None
        try:
            root = jobs.create_ember(base, tpl, eid, bindings, NOW_FN(), name=title, origin="forge")
        except ValueError as e:
            return None, [f"Not built: {e}."]
    queued = SCHEDULER.request(eid, RUN_SETS["run"]) if SCHEDULER else []
    left = [f"{s['type']} {s['value']}".strip() for s in out["draft"]["sources"] if not s["ok"]]
    notes = [f"Built ember {eid} in {root}. Its first run is " + ("queued." if queued else "waiting for the scheduler.")]
    if left:
        notes.append("Left out: " + "; ".join(left) + ".")
    return {"id": eid, "name": title or tpl["title"], "root": root, "queued": queued}, notes


def post_forge(req, cfg):
    """One Forge interview turn; builds the ember when the model says the user agreed."""
    msgs, built = _forge_body(req.body)
    router = ROUTER_CLS(cfg)
    try:
        loaded = router.loaded_ids(MAIN_FN())
    except LLMError as ex:
        raise Error(503, f"the llama.cpp router isn't answering ({_msg(ex)})") from None
    wanted = req.body.get("model")
    model = wanted if wanted in loaded else (loaded[0] if loaded else None)
    if not model:
        raise Error(409, "Load a model in LlamaForge first. Forge talks with a model that is already "
                         "loaded (a 12B+ instruct model works best) and never loads one itself.")
    n_ctx = clamp_n_ctx(router.n_ctx(model))
    if n_ctx < jobs.MIN_N_CTX:
        raise Error(409, f"{model} has a {n_ctx}-token context; Forge needs at least {jobs.MIN_N_CTX}")
    ctx = {"today": NOW_FN(), "embers": [eid for eid, _ in list_embers(embers_dir(cfg))]}
    try:
        out = forge.turn(router.llm(model, autoload=False), msgs, req.body.get("draft"), ctx, n_ctx)
    except RouterUnavailable as ex:
        raise Error(503, _msg(ex)) from None
    except LLMError as ex:
        raise Error(502, f"{model} gave a reply that couldn't be used ({_msg(ex)}). Try again, "
                         "or load a larger model.") from None
    except ValueError as ex:
        raise Error(400, str(ex)) from None
    made, notes = _forge_build(cfg, out, model, built) if out["build"] else (None, [])
    return 200, {"say": out["say"], "draft": out["draft"], "notes": notes, "built": made, "model": model}


def post_push_test(req, cfg):
    root = _root(cfg, req.body.get("id"))
    with jobs.Ember(root) as e:
        conf = dict(e.conf)
    try:
        push_conf = push.validate(conf.get("push"))
    except ValueError as ex:
        raise Error(400, str(ex)) from None
    if not push_conf:
        raise Error(400, "set up push first (ntfy topic or webhook address)")
    dates = _brief_dates(root)
    result = ({"path": os.path.join(root, "briefs", dates[0] + ".md")} if dates
              else {"headline": "Test from LlamaForge: this ember has no brief yet."})
    ok, error = push.deliver(root, conf, push_conf, result, NOW_FN(), PUSH_OPENER)
    return 200, {"ok": ok, "error": error}


def post_folder(req, cfg):
    """Point the panel at another embers folder ("" = the default).

    Nothing is moved: embers already in the old folder stay there, and
    switching back finds them again. The new folder is created when its
    parent exists, and must be writable."""
    v = req.body.get("path")
    if not isinstance(v, str):
        raise Error(400, FULL_PATH)
    v = os.path.expanduser(v.strip())
    old = embers_dir(cfg)
    if v:
        if not os.path.isabs(v):
            raise Error(400, FULL_PATH)
        v = os.path.normpath(v)
        if os.path.isfile(os.path.join(v, "ember.json")):
            raise Error(400, "that is one ember's own folder; pick the folder that holds embers")
        if os.path.exists(v) and not os.path.isdir(v):
            raise Error(400, "that is a file, not a folder")
        if not os.path.isdir(v):
            if not os.path.isdir(os.path.dirname(v)):
                raise Error(400, f"its parent folder doesn't exist: {os.path.dirname(v)}")
            try:
                os.mkdir(v)
            except OSError as ex:
                raise Error(400, f"can't create it: {_msg(ex)}") from None
        try:
            fd, probe = tempfile.mkstemp(dir=v, prefix=".lf-write-test-")
            os.close(fd)
            os.remove(probe)
        except OSError as ex:
            raise Error(400, f"can't write there: {_msg(ex)}") from None
    new = embers_dir(dict(cfg, embers_dir=v))
    SAVE_FN({"embers_dir": v})
    same = os.path.normcase(old) == os.path.normcase(new)
    return 200, {"ok": True, "embers_dir": new, "found": len(list_embers(new)),
                 "left": 0 if same else len(list_embers(old))}
