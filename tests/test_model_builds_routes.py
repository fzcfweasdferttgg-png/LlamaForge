"""Per-model builds through the routes: a model pinned to another build runs in
its own process (slotproc), single-model mode included, and every place that
asks "is it up, where, what did it log" knows about those processes."""
import conftest_paths  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import config
import routes
from routes import ApiError, Req


class FakeProcs:
    def __init__(self):
        self.recs = {}
        self.tails = {}

    def add(self, mid, state="ready", port=8100, rc=None):
        self.recs[mid] = {"state": state, "port": port, "pid": 4242, "exit_code": rc,
                          "endpoint": f"http://127.0.0.1:{port}", "bin": "/ik/llama-server",
                          "started_at": 1}

    def status(self):
        return {m: dict(r) for m, r in self.recs.items()}

    def has(self, mid):
        return mid in self.recs

    def log_tail(self, mid, lines=200):
        return self.tails.get(mid, "")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ik = os.path.join(self.tmp, "ik", "llama-server.exe")
        os.makedirs(os.path.dirname(self.ik))
        open(self.ik, "w").close()
        self.b7000 = os.path.join(self.tmp, "engines", "b7000-cuda", "llama-server.exe")
        os.makedirs(os.path.dirname(self.b7000))
        open(self.b7000, "w").close()
        self.installs = [{"dir": os.path.dirname(self.b7000), "server_bin": self.b7000,
                          "tag": "b7000"}]

        old = config.CONFIG
        config.CONFIG = os.path.join(self.tmp, "config.json")
        self.addCleanup(setattr, config, "CONFIG", old)
        self.write_cfg(router_port=8080, active_engine="llamacpp", multi_model=False,
                       server_bin=os.path.join(self.tmp, "router", "llama-server.exe"),
                       ik_llama_server_bin=self.ik, model_builds={"ikm": "ik_llama"})

        self.rstate = {"m1": "unloaded", "ikm": "unloaded"}
        self.router = mock.patch.object(routes, "router", side_effect=self.fake_router).start()
        self.pool = mock.patch.object(routes.router_ctl, "running_pool", return_value=None).start()
        self.slots = mock.patch.object(routes, "SLOTS").start()
        self.slots.load.return_value = (200, {"ok": True})
        self.slots.unload.side_effect = self.slot_unload
        self.procs = FakeProcs()
        mock.patch.object(routes, "PROCS", self.procs).start()
        self.list_installs = mock.patch.object(routes, "_installs",
                                               return_value=self.installs).start()
        self.sections = {"*": {"ctx-size": "8192"}, "m1": {"model": "/m/one.gguf"},
                         "ikm": {"model": "/m/bonsai.gguf"}}
        mock.patch.object(routes.config, "read_sections",
                          side_effect=lambda *a, **k: self.sections).start()
        mock.patch.object(routes.REGISTRY, "for_model",
                          return_value=routes.REGISTRY.get("llamacpp")).start()
        mock.patch.object(routes, "_sleep").start()
        self.addCleanup(mock.patch.stopall)

    def write_cfg(self, **c):
        with open(config.CONFIG, "w", encoding="utf-8") as f:
            json.dump(c, f)

    def fake_router(self, path, method="GET", body=None, timeout=30):
        if path == "/models":
            return 200, {"data": [{"id": m, "status": {"value": s}}
                                  for m, s in self.rstate.items()]}
        if path == "/models/unload":
            self.rstate[body["model"]] = "unloaded"
        if path == "/models/load":
            self.rstate[body["model"]] = "loaded"
        return 200, {"success": True}

    def slot_unload(self, mid):
        self.procs.recs.pop(mid, None)
        return 200, {"ok": True}

    def router_calls(self, path):
        return [c.args[2]["model"] for c in self.router.call_args_list
                if c.args[0] == path and len(c.args) > 2]


class PinLookup(Base):
    def test_an_unpinned_model_never_lists_the_installs(self):
        # the 4-second poll asks this for every load-able row
        self.assertEqual(routes.pinned_bin("m1"), (None, ""))
        self.list_installs.assert_not_called()

    def test_a_pin_resolves_through_builds(self):
        self.assertEqual(routes.pinned_bin("ikm"), (self.ik, ""))

    def test_pinned_installs_are_protected_from_pruning(self):
        self.write_cfg(model_builds={"m1": "b7000-cuda"})
        with mock.patch.object(routes.prebuilt, "list_installs", return_value=self.installs), \
                mock.patch.object(routes.config, "get_profiles", return_value={}):
            self.assertIn(os.path.dirname(self.b7000), routes.PREBUILT.protected())


class SingleModeLoads(Base):
    def test_a_pinned_model_loads_through_the_slot_manager(self):
        status, out = routes.post_load(Req(body={"model": "ikm"}))
        self.assertEqual(status, 200)
        self.assertTrue(out["success"])
        self.slots.load.assert_called_once_with("ikm", "main", False, wait=True)
        self.assertEqual(self.router_calls("/models/load"), [])

    def test_engine_agnostic_route_too(self):
        routes.post_model_load(Req(body={"model": "ikm"}))
        self.slots.load.assert_called_once_with("ikm", "main", False, wait=True)

    def test_a_dangling_pin_goes_to_the_slot_manager_to_be_refused(self):
        self.write_cfg(router_port=8080, model_builds={"m1": "b6000-gone"})
        self.slots.load.return_value = (409, {"ok": False, "reason": "build b6000-gone is no longer installed"})
        status, out = routes.post_load(Req(body={"model": "m1"}))
        self.assertEqual(status, 409)
        self.assertIn("no longer installed", out["error"]["message"])
        self.assertEqual(self.router_calls("/models/load"), [])

    def test_a_router_load_replaces_a_running_process(self):
        self.procs.add("ikm")
        status, out = routes.post_load(Req(body={"model": "m1"}))
        self.assertEqual(status, 200)
        self.slots.unload.assert_called_once_with("ikm")
        self.assertEqual(self.router_calls("/models/load"), ["m1"])

    def test_no_processes_is_the_plain_path(self):
        routes.post_load(Req(body={"model": "m1"}))
        self.slots.load.assert_not_called()
        self.slots.unload.assert_not_called()
        self.assertEqual(self.router_calls("/models/load"), ["m1"])

    def test_load_and_wait_for_a_pinned_model(self):
        routes._load_and_wait("ikm", "main")
        self.slots.load.assert_called_once_with("ikm", "main", False, wait=True)

    def test_load_and_wait_on_the_router_stops_processes_first(self):
        self.procs.add("ikm")
        routes._load_and_wait("m1", "main")
        self.slots.unload.assert_called_once_with("ikm")
        self.assertEqual(self.router_calls("/models/load"), ["m1"])


class Unloads(Base):
    def test_unloading_a_process_goes_through_the_slot_manager(self):
        self.procs.add("ikm")
        status, out = routes.post_unload(Req(body={"model": "ikm"}))
        self.assertEqual(status, 200)
        self.slots.unload.assert_called_once_with("ikm")
        self.assertEqual(self.router_calls("/models/unload"), [])

    def test_engine_agnostic_route_too(self):
        self.procs.add("ikm")
        routes.post_model_unload(Req(body={"model": "ikm"}))
        self.slots.unload.assert_called_once_with("ikm")

    def test_unload_all_includes_processes(self):
        self.rstate["m1"] = "loaded"
        self.procs.add("ikm")
        status, out = routes.post_unload_all(Req())
        self.assertEqual(sorted(out["unloaded"]), ["ikm", "m1"])
        self.assertEqual(self.router_calls("/models/unload"), ["m1"])
        self.slots.unload.assert_called_once_with("ikm")

    def test_a_knob_save_restarts_a_running_process_through_the_slot_manager(self):
        self.procs.add("ikm")
        with mock.patch.object(routes.config, "set_keys"):
            running = routes._apply_knobs_and_reload("ikm", {"ctx-size": "4096"})
        self.assertTrue(running)
        self.slots.unload.assert_called_once_with("ikm")
        self.assertEqual(self.router_calls("/models/unload"), [])


class State(Base):
    def test_a_process_overlays_the_routers_row(self):
        self.procs.add("ikm", port=8101)
        rows = {m["id"]: m for m in routes.model_state()["models"]}
        r = rows["ikm"]
        self.assertEqual(r["status"], "loaded")
        self.assertFalse(r["failed"])
        self.assertEqual(r["endpoint"], "http://127.0.0.1:8101")
        self.assertEqual(r["process"]["port"], 8101)
        self.assertEqual(r["build"], "ik_llama")
        self.assertNotIn("build", rows["m1"])
        self.assertNotIn("process", rows["m1"])

    def test_loaded_rows_sort_first_with_the_overlay(self):
        self.procs.add("ikm")
        self.assertEqual(routes.model_state()["models"][0]["id"], "ikm")

    def test_a_process_that_exited_reads_failed(self):
        self.procs.add("ikm", state="failed", rc=1)
        r = next(m for m in routes.model_state()["models"] if m["id"] == "ikm")
        self.assertEqual(r["status"], "unloaded")
        self.assertTrue(r["failed"])
        self.assertEqual(r["process"]["exit_code"], 1)

    def test_the_backend_keeps_the_process_endpoint(self):
        self.procs.add("ikm", port=8102)
        self.rstate["m1"] = "loaded"
        rows = {m["id"]: m for m in routes.REGISTRY.get("llamacpp").state()["models"]}
        self.assertEqual(rows["ikm"]["endpoint"], "http://127.0.0.1:8102")
        self.assertEqual(rows["m1"]["endpoint"], "http://127.0.0.1:8080")

    def test_model_status_asks_the_process_first(self):
        self.procs.add("ikm", state="loading")
        self.assertEqual(routes._model_status("ikm")["value"], "loading")
        self.assertEqual(routes._model_status("m1")["value"], "unloaded")

    def test_diag_reads_the_processes_own_log(self):
        self.procs.add("ikm", state="failed", rc=1)
        self.procs.tails["ikm"] = "error loading model: unknown model architecture: 'zz'"
        with mock.patch.object(routes, "router_log_tail", return_value="") as rlog, \
                mock.patch.object(routes.diag, "diagnose", return_value={"error": "x"}) as dg:
            routes.get_model_diag(Req(qs={"model": "ikm"}))
        rlog.assert_not_called()
        self.assertIn("unknown model architecture", dg.call_args.args[0])

    def test_measure_goes_to_the_processes_port(self):
        self.procs.add("ikm", port=8103)
        self.assertEqual(routes._model_base("ikm"), "http://127.0.0.1:8103")
        self.assertEqual(routes._model_base("m1"), "http://127.0.0.1:8080")


class ClientConfig(Base):
    def test_a_process_model_gets_its_own_endpoint(self):
        self.procs.add("ikm", port=8104)
        with mock.patch.object(routes.clientsetup, "generate", return_value={}) as gen:
            status, out = routes.post_client_config(Req(body={"model": "ikm"}))
        self.assertEqual(gen.call_args.args[0], "http://127.0.0.1:8104")
        self.assertTrue(out["model_loaded"])

    def test_router_models_keep_the_router_endpoint(self):
        with mock.patch.object(routes.clientsetup, "generate", return_value={}) as gen:
            routes.post_client_config(Req(body={"model": "m1"}))
        self.assertEqual(gen.call_args.args[0], "http://127.0.0.1:8080")


class SetBuild(Base):
    def pins(self):
        return config.load().get("model_builds")

    def test_pin_and_clear(self):
        status, out = routes.post_model_build(Req(body={"model": "m1", "build": "b7000-cuda"}))
        self.assertEqual((status, out["ok"], out["changed"], out["was_running"]),
                         (200, True, True, False))
        self.assertEqual(self.pins(), {"ikm": "ik_llama", "m1": "b7000-cuda"})
        routes.post_model_build(Req(body={"model": "m1", "build": ""}))
        self.assertEqual(self.pins(), {"ikm": "ik_llama"})

    def test_an_unchanged_pin_touches_nothing(self):
        self.procs.add("ikm")
        status, out = routes.post_model_build(Req(body={"model": "ikm", "build": "ik_llama"}))
        self.assertFalse(out["changed"])
        self.slots.unload.assert_not_called()

    def test_a_running_model_is_unloaded_to_apply(self):
        self.rstate["m1"] = "loaded"
        status, out = routes.post_model_build(Req(body={"model": "m1", "build": "ik_llama"}))
        self.assertTrue(out["was_running"])
        self.assertEqual(self.router_calls("/models/unload"), ["m1"])
        self.assertEqual(self.pins()["m1"], "ik_llama")

    def test_a_running_process_is_stopped_to_apply(self):
        self.procs.add("ikm")
        status, out = routes.post_model_build(Req(body={"model": "ikm", "build": ""}))
        self.assertTrue(out["was_running"])
        self.slots.unload.assert_called_once_with("ikm")
        self.assertEqual(self.pins(), {})

    def test_refusals(self):
        for body, code in [({"model": "m1", "build": "b6000-gone"}, 400),
                           ({"model": "m1", "build": "../x"}, 400),
                           ({"model": "m1", "build": 7}, 400),
                           ({"model": "nope", "build": "ik_llama"}, 404),
                           ({"model": "*", "build": "ik_llama"}, 400),
                           ({"build": "ik_llama"}, 400)]:
            with self.assertRaises(ApiError) as e:
                routes.post_model_build(Req(body=body))
            self.assertEqual(e.exception.status, code, body)
        self.assertEqual(self.pins(), {"ikm": "ik_llama"})

    def test_ik_must_be_built_to_pin_it(self):
        os.remove(self.ik)
        with self.assertRaises(ApiError) as e:
            routes.post_model_build(Req(body={"model": "m1", "build": "ik_llama"}))
        self.assertIn("Build / Update", str(e.exception))


if __name__ == "__main__":
    unittest.main()
