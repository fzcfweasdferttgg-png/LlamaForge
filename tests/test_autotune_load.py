"""Autotune refine has to measure each candidate on its own config: the router
won't load a model that's already running (so a candidate matching the running
config used to be skipped), and on a pool every load goes through the planner.
Benchmarking isn't saving, either - models.ini and the model end up the way
they were found."""
import conftest_paths  # noqa: F401
import unittest
from unittest import mock

import routes
from routes import Req


class FakeRouter:
    """llama.cpp's router, as far as autotune can tell: loads and unloads finish
    a couple of polls later, and a model runs on the args the last reload gave
    it. The reload here only refreshes stopped models - the conservative case;
    current builds also bounce a running model whose preset changed."""

    def __init__(self, ini, running=False, fail=()):
        self.ini, self.fail = ini, set(fail)
        self.args = dict(ini["m"])            # what the router would launch it with
        self.state = "loaded" if running else "unloaded"
        self.live = dict(self.args) if running else None   # what the process runs on
        self.failed, self.ticks, self.calls = False, 0, []

    def _tick(self):
        if self.ticks:
            self.ticks -= 1
            if not self.ticks:
                if self.state == "loading":
                    if self.live.get("ubatch-size") in self.fail:
                        self.state, self.live, self.failed = "unloaded", None, True
                    else:
                        self.state = "loaded"
                elif self.state == "stopping":
                    self.state, self.live = "unloaded", None

    def __call__(self, path, method="GET", body=None, timeout=30):
        self.calls.append(path)
        if path == "/models":
            self._tick()
            status = {"value": "loaded" if self.state == "stopping" else self.state}
            if self.failed:
                status.update(failed=True, exit_code=1)
            return 200, {"data": [{"id": "m", "status": status}]}
        if path == "/models?reload=1":
            if self.state == "unloaded":
                self.args, self.failed = dict(self.ini["m"]), False
            return 200, {}
        if path == "/models/load":
            if self.state != "unloaded":
                return 400, {"error": {"message": "model is already running"}}
            self.state, self.live, self.ticks = "loading", dict(self.args), 2
            return 200, {"success": True}
        if path == "/models/unload":
            if self.state in ("loaded", "loading"):
                self.state, self.ticks = "stopping", 2
            return 200, {"success": True}
        return 404, {}

    def tok_s(self):
        """Speed depends on what the process actually runs on; nothing to
        measure unless it's serving."""
        if self.state != "loaded":
            return 0.0
        return {"512": 10.0, "1024": 30.0}.get(self.live.get("ubatch-size"), 20.0)


class Base(unittest.TestCase):
    def setUp(self):
        self.ini = {"m": {"model": "x.gguf", "ubatch-size": "256 ; mine"}}
        mock.patch.object(routes.config, "set_keys", side_effect=self._set_keys).start()
        mock.patch.object(routes.config, "read_sections",
                          side_effect=lambda path=None, raw=False: self._sections(raw)).start()
        mock.patch.object(routes, "_find_model", return_value={"id": "m"}).start()
        mock.patch.object(routes, "_sleep", lambda s: None).start()
        self.slots_on = mock.patch.object(routes, "_slots_on", return_value=False).start()
        self.slots = mock.patch.object(routes, "SLOTS").start()
        self.slots._main.return_value = ""
        self.addCleanup(mock.patch.stopall)

    def _set_keys(self, mid, updates):
        sec = self.ini.setdefault(mid, {})
        for k, v in updates.items():
            if v is None:
                sec.pop(k, None)
            else:
                sec[k] = v

    def _sections(self, raw):
        if raw:
            return {m: dict(s) for m, s in self.ini.items()}
        return {m: {k: v.split(";", 1)[0].strip() for k, v in s.items()}
                for m, s in self.ini.items()}

    def use(self, fake):
        self.fake = fake
        mock.patch.object(routes, "router", fake).start()
        mock.patch.object(routes, "_measure_tok_s", lambda mid: fake.tok_s()).start()

    def refine(self):
        return routes._autotune_refine({"model": "m", "intent": "speed",
                                        "knobs": {"ubatch-size": "512", "batch-size": "2048"}})


class PlainRouter(Base):
    def test_a_running_model_is_measured_on_every_candidate(self):
        self.use(FakeRouter(self.ini, running=True))
        out = self.refine()
        speeds = [(c["knobs"]["ubatch-size"], c["knobs"]["batch-size"], c["tok_s"])
                  for c in out["measurements"]["candidates"]]
        self.assertEqual(speeds, [("512", "2048", 10.0), ("1024", "2048", 30.0),
                                  ("512", "4096", 10.0)])
        self.assertEqual(out["knobs"]["ubatch-size"], "1024")
        self.assertNotIn("error", out)

    def test_a_stopped_model_is_measured_once_it_has_loaded(self):
        self.use(FakeRouter(self.ini, running=False))
        out = self.refine()
        self.assertEqual(out["measurements"]["chosen_tok_s"], 30.0)
        self.assertEqual(len(out["measurements"]["candidates"]), 3)

    def test_the_ini_goes_back_to_what_the_user_wrote(self):
        self.use(FakeRouter(self.ini, running=False))
        self.refine()
        self.assertEqual(self.ini["m"], {"model": "x.gguf", "ubatch-size": "256 ; mine"})

    def test_a_model_found_stopped_is_left_stopped(self):
        self.use(FakeRouter(self.ini, running=False))
        self.refine()
        self.assertEqual(self.fake.state, "unloaded")

    def test_a_model_found_running_runs_again_on_its_own_config(self):
        self.use(FakeRouter(self.ini, running=True))
        out = self.refine()
        self.assertEqual(self.fake.state, "loaded")
        self.assertEqual(self.fake.live["ubatch-size"], "256 ; mine")
        self.assertNotIn("batch-size", self.fake.live)
        self.assertNotIn("restore_error", out)

    def test_a_candidate_that_fails_to_load_is_skipped(self):
        self.use(FakeRouter(self.ini, running=False, fail={"1024"}))
        out = self.refine()
        tried = [c["knobs"]["ubatch-size"] for c in out["measurements"]["candidates"]]
        self.assertEqual(tried, ["512", "512"])

    def test_when_nothing_loads_the_reason_comes_back(self):
        self.use(FakeRouter(self.ini, running=False, fail={"512", "1024"}))
        out = self.refine()
        self.assertIn("exit code 1", out["error"])
        self.assertEqual(out["knobs"]["ubatch-size"], "512")


class Save(Base):
    def test_save_lands_even_where_a_reload_skips_running_models(self):
        self.use(FakeRouter(self.ini, running=True))
        status, out = routes.post_save(Req(body={"model": "m",
                                                 "settings": {"ubatch-size": "1024"}}))
        self.assertTrue(out["was_running"])
        self.assertEqual(self.fake.args["ubatch-size"], "1024")


class SlotsPool(Base):
    def setUp(self):
        super().setUp()
        self.slots_on.return_value = True
        self.use(FakeRouter(self.ini, running=False))
        self.slots.load.return_value = (200, {"ok": True})
        self.slots.unload.return_value = (200, {"ok": True})

    def test_loads_go_through_the_planner(self):
        self.refine()
        self.assertEqual(self.slots.load.call_count, 3)
        self.assertNotIn("/models/load", self.fake.calls)

    def test_with_no_main_the_tuned_model_is_the_main(self):
        self.refine()
        self.slots.load.assert_called_with("m", "main", False, wait=True)

    def test_beside_another_main_it_is_tuned_as_a_worker(self):
        self.slots._main.return_value = "big"
        self.refine()
        self.slots.load.assert_called_with("m", "worker", False, wait=True)

    def test_the_main_stays_the_main(self):
        self.slots._main.return_value = "m"
        self.fake.state, self.fake.live = "loaded", dict(self.fake.args)
        self.slots.unload.side_effect = lambda mid: (
            setattr(self.fake, "state", "unloaded") or (200, {"ok": True}))
        self.refine()
        for call in self.slots.load.call_args_list:
            self.assertEqual(call.args[1], "main")

    def test_a_refusal_is_the_reason(self):
        self.slots.load.return_value = (409, {"ok": False, "reason": "needs ~9.0 GiB"})
        out = self.refine()
        self.assertIn("GiB", out["error"])


if __name__ == "__main__":
    unittest.main()
