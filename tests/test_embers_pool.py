"""Embers on a multi-model pool: a job runs on whichever model it needs if
that one is up, loads its own as a worker beside the user's models when the
planner says it fits (and unloads it afterwards), and never evicts anything -
the router's own load is never used, since it would bypass the planner's
device pins."""
import conftest_paths  # noqa: F401
import unittest

from embers.llm import LLMError
from test_embers_scheduler import NOW, Runners, SchedCase
from embers import scheduler


class PoolState:
    def __init__(self):
        self.up = {"main": "loaded"}        # id -> "loaded" | "sleeping" | "loading"
        self.busy = {}                      # id -> in-flight requests
        self.down = False
        self.calls = []
        self.probe_errors = []              # /metrics or /props asked about a model that isn't up
        self.on_complete = None


class PoolRouter:
    def __init__(self, s, cfg):
        self.s = s

    def models(self):
        if self.s.down:
            raise LLMError("connection refused")
        return [{"id": m, "status": st, "failed": False} for m, st in self.s.up.items()]

    def loaded_entries(self, main=""):
        up = [m for m in self.models() if m["status"] in ("loaded", "sleeping")]
        return sorted(up, key=lambda m: m["id"] != main)

    def loaded_ids(self, main=""):
        return [m["id"] for m in self.loaded_entries(main)]

    def loaded_entry(self):
        e = self.loaded_entries()
        return e[0] if e else None

    def loaded_model(self):
        e = self.loaded_entry()
        return e["id"] if e else None

    def report(self, model):
        self.s.calls.append(("report", model))
        if self.s.up.get(model) != "loaded":    # /metrics wakes a sleeper, loads a stranger
            self.s.probe_errors.append(("report", model))
        return {"busy": self.s.busy.get(model, 0), "work": 0}

    def n_ctx(self, model):
        if self.s.up.get(model) not in ("loaded", "sleeping"):
            self.s.probe_errors.append(("n_ctx", model))
        return 16384

    def load(self, model):
        self.s.calls.append(("router-load", model))

    def unload(self, model):
        self.s.calls.append(("router-unload", model))

    def wait_status(self, *a, **kw):
        self.s.calls.append(("wait_status",))
        return True

    def complete(self, model, autoload=True):
        def call(messages, schema, max_tokens):
            self.s.calls.append(("chat", model))
            if self.s.on_complete:
                self.s.on_complete()
            return "{}", {}
        return call


class FakePool:
    def __init__(self, s):
        self.s, self.on, self.main_id = s, True, "main"
        self.verdict = {"ok": True}
        self.load_result = None             # (code, body); None loads it
        self.calls = []

    def active(self):
        return self.on

    def plan(self, mid):
        self.calls.append(("plan", mid))
        return dict(self.verdict)

    def load(self, mid):
        self.calls.append(("load", mid))
        if self.load_result is not None:
            return self.load_result
        self.s.up[mid] = "loaded"
        return 200, {"ok": True}

    def unload(self, mid):
        self.calls.append(("unload", mid))
        self.s.up.pop(mid, None)
        return 200, {"ok": True}

    def touch(self, mid):
        self.calls.append(("touch", mid))

    def main(self):
        return self.main_id


class PoolCase(SchedCase):
    def make_sched(self):
        self.ps = PoolState()
        self.pool = FakePool(self.ps)
        return scheduler.Scheduler(lambda: dict(self.cfg),
                                   router_cls=lambda cfg: PoolRouter(self.ps, cfg),
                                   now=lambda: self.now[0], clock=lambda: self.t[0],
                                   sleep=lambda s: None, runners=self.runners.table(),
                                   pool=self.pool)

    def tearDown(self):
        self.assertEqual(self.ps.probe_errors, [], "probed a model that isn't up")
        self.assertEqual([c for c in self.ps.calls if c[0].startswith("router-")], [],
                         "used the router's own load/unload on a pool")

    def model_used(self):
        return self.runners.calls[0][2]

    def pool_ops(self):
        return [c for c in self.pool.calls if c[0] in ("load", "unload")]


class RunOnThePool(PoolCase):
    def test_unpinned_runs_on_the_main(self):
        self.ps.up = {"small": "loaded", "main": "loaded"}
        self.ember("a")
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))
        self.assertEqual(self.model_used(), "main")
        self.assertEqual(self.pool_ops(), [])

    def test_a_loaded_worker_runs_while_the_main_works(self):
        self.ps.up = {"main": "loaded", "small": "loaded"}
        self.ps.busy = {"main": 2}
        self.ember("a", model="small")
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))
        self.assertEqual(self.model_used(), "small")
        self.assertEqual(self.pool_ops(), [])
        self.assertIn(("touch", "small"), self.pool.calls)

    def test_a_busy_model_waits(self):
        self.ps.busy = {"main": 1}
        self.ember("a")
        r = self.sched.tick()
        self.assertIsNone(r["ran"])
        self.assertEqual(r["waiting"], {"a": {"ingest": "main is busy"}})

    def test_a_sleeping_model_is_idle_and_never_probed(self):
        self.ps.up = {"main": "sleeping"}
        self.ember("a")
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))

    def test_router_down_waits(self):
        self.ps.down = True
        self.ember("a")
        self.assertEqual(self.sched.tick()["waiting"],
                         {"a": {"ingest": "the router is not reachable"}})


class LoadBeside(PoolCase):
    def test_loads_beside_runs_and_unloads(self):
        self.ember("a", model="q")
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))
        self.assertEqual(self.model_used(), "q")
        self.assertEqual(self.pool.calls[0], ("plan", "q"))
        self.assertEqual(self.pool_ops(), [("load", "q"), ("unload", "q")])
        self.assertEqual(self.ps.up, {"main": "loaded"})

    def test_no_idle_minutes_needed(self):
        self.ember("a", model="q")
        self.t[0] = 0.0                       # a fresh scheduler, nothing observed yet
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))

    def test_the_planners_refusal_is_the_wait_reason(self):
        self.pool.verdict = {"ok": False, "reason": "needs ~9.0 GiB"}
        self.ember("a", model="q")
        r = self.sched.tick()
        self.assertIsNone(r["ran"])
        self.assertEqual(r["waiting"]["a"]["ingest"],
                         "q can't load beside the loaded models: needs ~9.0 GiB")
        self.assertEqual(self.pool_ops(), [])

    def test_nothing_loads_while_anything_generates(self):
        self.ps.busy = {"main": 1}
        self.ember("a", model="q")
        r = self.sched.tick()
        self.assertEqual(r["waiting"]["a"]["ingest"], "the router is busy")
        self.assertEqual(self.pool.calls, [])      # not even a plan

    def test_swapping_off_means_no_loads(self):
        self.cfg["embers_swap_models"] = False
        self.ember("a", model="q")
        r = self.sched.tick()
        self.assertEqual(r["waiting"]["a"]["ingest"], "needs q; model swapping is off")
        self.assertEqual(self.pool.calls, [])

    def test_a_refusal_at_load_time_waits(self):
        self.pool.load_result = (409, {"ok": False, "reason": "big is still loading"})
        root = self.ember("a", model="q")
        r = self.sched.tick()
        self.assertIsNone(r["ran"])
        self.assertIn("big is still loading", r["waiting"]["a"]["ingest"])
        self.assertEqual(self.runs(root), [])

    def test_a_failed_load_skips_the_job(self):
        self.pool.load_result = (500, {"ok": False, "error": "the router reports the load failed"})
        self.ember("a", model="q")
        r = self.sched.tick()
        self.assertIsNone(r["ran"])
        self.assertEqual(len(r["skipped"]), 1)
        self.assertIn("load failed", r["skipped"][0][2])

    def test_a_router_error_object_is_readable(self):
        self.pool.load_result = (400, {"ok": False, "error": {"message": "bad preset"}})
        self.ember("a", model="q")
        self.assertIn("bad preset", self.sched.tick()["skipped"][0][2])

    def test_still_loading_waits(self):
        self.pool.load_result = (200, {"ok": True, "already": True, "loading": True})
        self.ember("a", model="q")
        r = self.sched.tick()
        self.assertIsNone(r["ran"])
        self.assertEqual(r["waiting"]["a"]["ingest"], "q is loading")

    def test_up_already_is_not_ours_to_unload(self):
        self.pool.load_result = (200, {"ok": True, "already": True})
        self.ps.up["q"] = "loaded"            # loaded by the user between the look and the load
        self.ember("a", model="q")
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))
        self.assertNotIn(("unload", "q"), self.pool.calls)


class AfterTheJob(PoolCase):
    def test_made_the_main_meanwhile_stays(self):
        self.ember("a", model="q")
        self.runners.on_run = lambda eid, job: setattr(self.pool, "main_id", "q")
        self.sched.tick()
        self.assertNotIn(("unload", "q"), self.pool.calls)

    def test_in_use_by_someone_else_stays(self):
        self.ember("a", model="q")
        self.runners.on_run = lambda eid, job: self.ps.busy.update(q=1)
        self.sched.tick()
        self.assertNotIn(("unload", "q"), self.pool.calls)

    def test_evicted_mid_run_is_preemption(self):
        self.ember("a", model="q")
        self.runners.llm_calls = 1
        self.runners.on_run = lambda eid, job: self.ps.up.pop("q")
        r = self.sched.tick()
        self.assertEqual(r["ran"], ("a", "ingest", "failed"))
        self.assertIn("pre-empted: q was unloaded", str(self.runners.llm_errors[0]))
        self.assertNotIn(("unload", "q"), self.pool.calls)

    def test_a_job_that_raises_still_unloads(self):
        self.ember("a", model="q")
        self.runners.raise_for = {"a"}
        self.sched.tick()
        self.assertIn(("unload", "q"), self.pool.calls)

    def test_model_calls_go_out_while_ours_is_up(self):
        self.ember("a", model="q")
        self.runners.llm_calls = 2
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))
        self.assertEqual([c for c in self.ps.calls if c[0] == "chat"], [("chat", "q")] * 2)


class PoolOff(PoolCase):
    def test_an_inactive_pool_is_the_single_model_path(self):
        self.pool.on = False
        self.ember("a")
        self.assertEqual(self.sched.tick()["ran"], ("a", "ingest", "ok"))
        self.assertEqual(self.pool.calls, [])

    def test_a_broken_pool_check_is_not_a_pool(self):
        def boom():
            raise RuntimeError("pidfile")
        self.pool.active = boom
        self.ember("a")
        r = self.sched.tick()
        self.assertEqual(r["ran"], ("a", "ingest", "ok"))
        self.assertIn("pidfile", self.sched.status()["last_error"])


if __name__ == "__main__":
    unittest.main()
