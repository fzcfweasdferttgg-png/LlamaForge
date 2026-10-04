"""The panel's ember scheduler: one background thread that runs due jobs.

Every TICK seconds it lists the embers, works out which jobs are due
(sched.due_jobs) and asks sched.decide what to do about the router. It runs
at most one job per tick, under the ember's cross-process lock, so the CLI
and the panel never run one ember at once.

Sharing the GPU: the router is only contacted when something is due. A job
waits while the router is busy or down; a job pinned to another model may
swap it in after SWAP_IDLE seconds of observed idleness (never in the first
SWAP_IDLE seconds after start) and puts the previous model back afterwards,
unless the user loaded something else meanwhile. activity() and n_ctx() are
only ever asked about the loaded model (asking about another one makes the
router load it).

Everything that can go wrong is caught: tick() never raises, a broken ember
never stops the others, and the last problem is kept in status().
"""
import copy, datetime as dt, os, threading, time

from . import embers_dir, jobs, lock, reserved_name
from .llm import LLMError, Router, RouterUnavailable, clamp_n_ctx
from .sched import ORDER, decide, due_jobs, next_occurrence
from .store import parse_ts

TICK = 60                  # s between passes when nothing ran
STOP_JOIN = 5              # s stop() waits for the thread
ERROR_CHARS = 400
RUNNERS = {"ingest": jobs.ingest, "brief": jobs.brief, "lint": jobs.lint}
LOCKED = "running in another process"
_UNSEEN = object()         # no router observation yet


def list_embers(base):
    """[(id, root)] for ember folders under base: valid id, not reserved, has ember.json."""
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        root = os.path.join(base, name)
        if (jobs.ID_RE.fullmatch(name) and not reserved_name(name)
                and os.path.isfile(os.path.join(root, "ember.json"))):
            out.append((name, root))
    return out


def jobs_conf(conf):
    """The ember's schedule: a top-level "jobs" override, else the template's."""
    j = conf.get("jobs") if isinstance(conf, dict) else None
    if isinstance(j, dict):
        return j
    tpl = conf.get("template") if isinstance(conf, dict) else None
    j = tpl.get("jobs") if isinstance(tpl, dict) else None
    return j if isinstance(j, dict) else {}


def _msg(e):
    return f"{type(e).__name__}: {e}"[:ERROR_CHARS]


class Scheduler:
    def __init__(self, cfg_fn, router_cls=Router, now=dt.datetime.now, clock=time.monotonic,
                 sleep=time.sleep, runners=None):
        self.cfg_fn, self.router_cls = cfg_fn, router_cls
        self.now, self.clock, self.sleep = now, clock, sleep
        self.runners = dict(runners if runners is not None else RUNNERS)
        self._lock = threading.Lock()          # guards _status and _last_error
        self._tick_lock = threading.Lock()     # one tick at a time
        self._status, self._last_error = {}, None
        self._waiting = {}                     # (id, job) -> {"since": clock|None, "reason": str}
        self._last_active = clock()            # construction counts as activity: no swap for SWAP_IDLE
        self._last_loaded = _UNSEEN
        self._thread, self._stop = None, threading.Event()

    # ------------------------------------------------------------ public
    def status(self):
        with self._lock:
            out = copy.deepcopy(self._status)
            out["last_error"] = self._last_error
        return out

    def tick(self):
        with self._tick_lock:
            result = {"ran": None, "waiting": {}, "skipped": [], "error": None}
            try:
                self._tick(result)
            except Exception as e:             # tick never raises
                result["error"] = _msg(e)
                self._error(result["error"])
            return result

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="embers-scheduler")
        self._thread.start()

    def stop(self, timeout=STOP_JOIN):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    # ------------------------------------------------------------ internals
    def _loop(self):
        while not self._stop.is_set():
            ran = False
            try:
                r = self.tick()
                ran = bool(r.get("ran")) and not r.get("error")
            except Exception as e:             # tick() catches already; belt and braces
                self._error(_msg(e))
            if not ran:
                self._stop.wait(TICK)

    def _error(self, msg):
        with self._lock:
            self._last_error = str(msg)[:ERROR_CHARS]

    def _set_running(self, eid, job):
        with self._lock:
            if eid in self._status:
                self._status[eid]["running"] = job

    def _publish(self, status):
        with self._lock:
            self._status = status

    def _tick(self, result):
        cfg = self.cfg_fn()
        if not isinstance(cfg, dict):
            cfg = {}
        if cfg.get("embers_scheduler") is False:
            self._publish({})
            self._waiting.clear()
            return
        now = self.now()
        status, candidates = {}, []
        for eid, root in list_embers(embers_dir(cfg)):
            try:
                cand = self._scan(eid, root, now, status)
            except Exception as e:             # one broken ember must not stop the others
                self._error(f"{eid}: {_msg(e)}")
                continue
            if cand:
                candidates.append(cand)
        keys = {(c["id"], c["job"]) for c in candidates}
        self._waiting = {k: v for k, v in self._waiting.items() if k in keys}
        self._publish(status)
        if not candidates:
            return                             # nothing due: never touch the router

        router, loaded, busy = self._observe(cfg)
        idle_for = self.clock() - self._last_active
        swap_ok = cfg.get("embers_swap_models", True)
        for c in candidates:
            key = (c["id"], c["job"])
            w = self._waiting.get(key)
            waited = self.clock() - w["since"] if w and w["since"] is not None else 0
            action, arg = decide(c["model"], loaded, busy, idle_for, waited, swap_ok)
            if action == "wait":
                self._wait(result, c, arg)
                continue
            if action == "skip":
                self._skip(result, c, now, arg)
                return
            self._act(result, c, router, loaded, action, arg)
            return

    def _scan(self, eid, root, now, status):
        """Status entry for one ember; its first due job as a candidate, or None."""
        with jobs.Ember(root) as ember:
            conf = ember.conf if isinstance(ember.conf, dict) else {}
            sched = jobs_conf(conf)
            enabled = conf.get("enabled") is not False
            nxt = {}
            for job in ORDER:
                occ = next_occurrence(sched.get(job), now)
                if occ is not None:
                    nxt[job] = occ.isoformat(timespec="minutes")
            status[eid] = {"enabled": enabled, "next": nxt if enabled else {},
                           "waiting": {}, "running": None}
            if not enabled:
                return None
            attempts = {}
            for job in ORDER:
                last = ember.store.last_attempt(job)
                attempts[job] = parse_ts(last["started"]) if last else None
            due = due_jobs(sched, attempts, parse_ts(conf.get("created")) or dt.datetime.min, now)
        if not due:
            return None
        model = conf.get("model")
        return {"id": eid, "root": root, "job": due[0],
                "model": model if isinstance(model, str) else ""}

    def _observe(self, cfg):
        """(router|None, loaded, busy) once per tick; busy None means unreachable."""
        router, loaded, busy = None, None, None
        try:
            router = self.router_cls(cfg)
            loaded = router.loaded_model()
            if not (isinstance(loaded, str) and loaded):
                loaded = None
            busy = router.activity(loaded) if loaded else 0
        except LLMError:
            busy, loaded = None, None
        except Exception as e:                 # a router bug is still "not reachable"
            self._error(f"router: {_msg(e)}")
            busy, loaded = None, None
        if busy is None or busy or (self._last_loaded is not _UNSEEN and loaded != self._last_loaded):
            self._last_active = self.clock()
        if busy is not None:
            self._last_loaded = loaded
        return router, loaded, busy

    def _wait(self, result, c, reason, count=True):
        key = (c["id"], c["job"])
        w = self._waiting.get(key)
        since = w["since"] if w else None
        if count and since is None:
            since = self.clock()
        self._waiting[key] = {"since": since, "reason": reason}
        result["waiting"].setdefault(c["id"], {})[c["job"]] = reason
        with self._lock:
            if c["id"] in self._status:
                self._status[c["id"]]["waiting"][c["job"]] = reason

    def _skip(self, result, c, now, reason):
        with jobs.Ember(c["root"]) as ember:
            with ember.store.db:
                ember.store.record_skip(c["job"], now, reason)
        self._waiting.pop((c["id"], c["job"]), None)
        result["skipped"].append((c["id"], c["job"], reason))

    def _act(self, result, c, router, loaded, action, model):
        try:
            with lock.held(c["root"]):
                self._locked_run(result, c, router, loaded, action, model)
        except lock.Busy:
            self._wait(result, c, LOCKED, count=False)   # the other runner writes the attempt row

    def _locked_run(self, result, c, router, prev, action, model):
        with jobs.Ember(c["root"]) as ember:
            # Re-check under the lock: another runner may have just done this slot.
            now = self.now()
            last = ember.store.last_attempt(c["job"])
            conf = ember.conf if isinstance(ember.conf, dict) else {}
            due = due_jobs(jobs_conf(conf), {c["job"]: parse_ts(last["started"]) if last else None},
                           parse_ts(conf.get("created")) or dt.datetime.min, now)
            if c["job"] not in due:
                self._waiting.pop((c["id"], c["job"]), None)
                return
            swapped = False
            try:
                if action == "swap":
                    swapped = True
                    try:
                        router.load(model)
                        ok = router.wait_status(model, "loaded", sleep=self.sleep, clock=self.clock)
                    except RouterUnavailable as e:
                        self._wait(result, c, f"could not load {model}: {e}"[:ERROR_CHARS])
                        return
                    except LLMError as e:
                        ok, why = False, f"could not load {model}: {e}"
                    else:
                        why = f"could not load {model}"
                    if not ok:
                        with ember.store.db:
                            ember.store.record_skip(c["job"], now, why[:ERROR_CHARS])
                        self._waiting.pop((c["id"], c["job"]), None)
                        result["skipped"].append((c["id"], c["job"], why[:ERROR_CHARS]))
                        return
                self._waiting.pop((c["id"], c["job"]), None)
                self._run(result, c, ember, router, model)
            finally:
                if swapped:
                    self._restore(router, prev, model)

    def _run(self, result, c, ember, router, model):
        self._set_running(c["id"], c["job"])
        try:
            n_ctx = clamp_n_ctx(router.n_ctx(model))
            r = self.runners[c["job"]](ember, router.llm(model), self.now(), n_ctx=n_ctx)
            status = r.get("status") if isinstance(r, dict) else None
            result["ran"] = (c["id"], c["job"], status if isinstance(status, str) else "unknown")
            if isinstance(r, dict) and r.get("router_down") is True:
                self._error(f"{c['id']} {c['job']}: the router went down during the run")
        except Exception as e:                 # the job closed its own run row
            result["ran"] = (c["id"], c["job"], "error")
            result["error"] = f"{c['id']} {c['job']}: {_msg(e)}"[:ERROR_CHARS]
            self._error(result["error"])
        finally:
            self._set_running(c["id"], None)

    def _restore(self, router, prev, want):
        """Put the GPU back as we found it, unless the user loaded another model
        meanwhile. Never raises; problems go to last_error."""
        try:
            cur = router.loaded_model()
            if not (isinstance(cur, str) and cur):
                cur = None
            if cur == want or cur is None:     # None: our load evicted prev, then failed
                if prev and prev != cur:
                    router.load(prev)
                elif cur == want:
                    router.unload(want)
        except Exception as e:
            self._error(f"could not restore {prev or 'the previous state'} after {want}: {_msg(e)}")
        finally:
            self._last_active = self.clock()   # the next swap needs fresh idle minutes
            self._last_loaded = _UNSEEN
