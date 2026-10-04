"""The panel's ember scheduler: one background thread that runs due jobs.

Every TICK seconds it lists the embers, works out which jobs are due
(sched.due_jobs) and asks sched.decide what to do about the router. It runs
at most one job per tick, under the ember's cross-process lock, so the CLI
and the panel never run one ember at once.

Sharing the GPU: the router is only contacted when something is due. A job
waits while the router is busy or down; a job pinned to another model may
swap it in after SWAP_IDLE seconds of observed idleness and puts the previous
model back afterwards, unless the user loaded something else meanwhile.
Idleness is only ever observed, never assumed: the idle clock runs between
two looks at the same loaded model that show no work in between (the
router's decode counter did not move; without the counter, two idle looks at
most 2*TICK apart). Time nobody watched counts as activity, so the first look
after start or after a quiet spell never swaps. report() and n_ctx() are only
ever asked about the loaded model (asking about another one makes the router
load it), and never about a sleeping one (/metrics wakes it).

Pre-emption: before every model request (ask_json's retry too) the job checks
that our model is still the loaded one (sleeping counts), and requests go out
with autoload=false, so even a switch between the check and the request
never loads ours back over theirs. If the user loaded another model, or
unloaded ours, the call raises RouterUnavailable, so the job stops cleanly
(its raws stay pending). The GPU is then theirs: no restore. The end of any job, like a
swap, counts as router activity, so the next swap needs fresh idle minutes.

Everything that can go wrong is caught: tick() never raises, a broken ember
never stops the others, and the last problem is kept in status().

Known limits: each ember has one wait clock at a time, so its later due jobs
start waiting (and count towards WAIT_MAX) only once the earlier job ran or
was skipped; behind a router that stays busy, an ember with ingest and brief
due can wait about two WAIT_MAX periods before both are skipped. And
`embers_cli tick` builds a fresh Scheduler, which has observed no idle time,
so it never swaps models.
"""
import copy, datetime as dt, os, threading, time

from . import embers_dir, jobs, lock, reserved_name
from .llm import (MAX_REPLY_CAP, LLMError, ModelNotLoaded, Router, RouterUnavailable, ask_json,
                  clamp_n_ctx)
from .sched import ORDER, decide, due_jobs, next_occurrence
from .store import parse_ts

TICK = 60                  # s between passes when nothing ran
STOP_JOIN = 5              # s stop() waits for the thread
ERROR_CHARS = 400
RUNNERS = {"ingest": jobs.ingest, "brief": jobs.brief, "lint": jobs.lint}
LOCKED = "running in another process"


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


def _quiet_since(prev, now, loaded, status, work):
    """True if nothing can have used the router between the previous look and this one."""
    if prev is None:
        return False                           # nobody watched before this look
    p_time, p_loaded, p_status, p_work = prev
    if p_loaded != loaded:
        return False
    if loaded is None:
        return True                            # nothing loaded both times: the GPU is free
    if work is not None and p_work is not None:
        return work == p_work                  # the decode counter misses nothing
    if status == "sleeping" and p_status == "sleeping":
        return True                            # asleep both times: nobody used it
    return now - p_time <= 2 * TICK            # gauge only: trust close looks alone


def _msg(e):
    return f"{type(e).__name__}: {e}"[:ERROR_CHARS]


class Scheduler:
    def __init__(self, cfg_fn, router_cls=Router, now=dt.datetime.now, clock=time.monotonic,
                 sleep=None, runners=None):
        self.cfg_fn, self.router_cls = cfg_fn, router_cls
        self._stop = threading.Event()
        # The default sleep is the stop event, so stop() cuts short a wait for a model load.
        self.now, self.clock, self.sleep = now, clock, sleep or self._stop.wait
        self.runners = dict(runners if runners is not None else RUNNERS)
        self._lock = threading.Lock()          # guards _status and _last_error
        self._tick_lock = threading.Lock()     # one tick at a time
        self._status, self._last_error = {}, None
        self._waiting = {}                     # (id, job) -> {"since": clock|None, "reason": str}
        self._last_active = clock()            # construction counts as activity: no swap for SWAP_IDLE
        self._last_seen = None                 # (clock, loaded, status, work) of the last good look
        self._thread = None

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
        """(router|None, loaded, busy) once per tick; busy None means unreachable.
        Moves the idle clock: see the module docstring."""
        router, loaded, status, busy, work = None, None, None, None, None
        try:
            router = self.router_cls(cfg)
            entry = router.loaded_entry()
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
                loaded, status = entry["id"], entry.get("status")
            if loaded is None or status == "sleeping":
                busy = 0                       # asleep is idle, and /metrics would wake it
            else:
                rep = router.report(loaded)
                busy, work = rep.get("busy"), rep.get("work")
        except LLMError:
            busy, loaded = None, None
        except Exception as e:                 # a router bug is still "not reachable"
            self._error(f"router: {_msg(e)}")
            busy, loaded = None, None
        now = self.clock()
        prev, self._last_seen = self._last_seen, None
        if busy is not None:
            self._last_seen = (now, loaded, status, work)
        if busy != 0 or not _quiet_since(prev, now, loaded, status, work):
            self._last_active = now
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
            swapped, preempted = False, []
            try:
                if action == "swap":
                    try:
                        router.load(model)
                        swapped = True             # only now is there anything to put back
                        ok = router.wait_status(model, "loaded", sleep=self.sleep, clock=self.clock,
                                                cancelled=self._stop.is_set)
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
                self._run(result, c, ember, router, model, preempted)
            finally:
                if swapped and not preempted:      # pre-empted: the GPU is the user's now
                    self._restore(router, prev, model)

    def _guarded_llm(self, router, model, preempted):
        """Like router.llm(model), but every request (ask_json's retry too) first
        checks our model is still loaded, and goes out with autoload=false."""
        complete = router.complete(model, autoload=False)

        def check():
            try:
                cur = router.loaded_model()    # sleeping counts: a request wakes it, still ours
            except LLMError as e:
                raise RouterUnavailable(f"could not check the loaded model: {e}"[:ERROR_CHARS]) from None
            if cur != model:
                who = cur if isinstance(cur, str) and cur else "nothing"
                preempted.append(who)
                raise RouterUnavailable(f"pre-empted: {who} is loaded now"[:ERROR_CHARS])

        def guarded(messages, schema, max_tokens):
            check()
            try:
                return complete(messages, schema, max_tokens)
            except ModelNotLoaded:
                check()                        # names whoever took the GPU
                preempted.append("nothing")
                raise RouterUnavailable("pre-empted: nothing is loaded now") from None
            except RouterUnavailable:
                raise
            except LLMError:
                check()                        # a switch mid-request is not the input's fault
                raise

        def call(messages, schema, max_tokens=2048):
            return ask_json(guarded, messages, schema, min(max_tokens, MAX_REPLY_CAP))
        call.model = model
        return call

    def _run(self, result, c, ember, router, model, preempted):
        self._set_running(c["id"], c["job"])
        try:
            n_ctx = clamp_n_ctx(router.n_ctx(model))
            llm = self._guarded_llm(router, model, preempted)
            r = self.runners[c["job"]](ember, llm, self.now(), n_ctx=n_ctx)
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
            self._last_active = self.clock()   # our own job counts as activity
            self._last_seen = None

    def _restore(self, router, prev, want):
        """Put the GPU back as we found it, unless the user loaded another model
        meanwhile. Never raises; problems go to last_error."""
        try:
            listed = router.models()
            if any(m.get("status") == "loading" and m.get("id") != want for m in listed):
                return                         # the user is loading something: the GPU is theirs
            up = next((m for m in listed if m.get("status") in ("loaded", "sleeping", "loading")), None)
            cur = up.get("id") if up else None
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
            self._last_seen = None
