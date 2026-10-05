"""The dashboard's load/unload routes go through the slot manager only on a
router LlamaForge started with a pool; everything else keeps the plain path."""
import conftest_paths  # noqa: F401
import unittest
from unittest import mock

import routes
from routes import Req

VERDICT_NO = {"ok": False, "reason": "needs ~20.0 GiB (predicted)", "devices": [],
              "place": None, "footprint": {}, "source": "predicted", "evict": ["w1"],
              "already": False, "evicted": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = {"router_port": 8080, "active_engine": "llamacpp", "multi_model": True}
        mock.patch.object(routes, "cfg", side_effect=lambda: dict(self.cfg)).start()
        self.pool = mock.patch.object(routes.router_ctl, "running_pool",
                                      return_value={"models_max": 3, "autoload": False}).start()
        self.slots = mock.patch.object(routes, "SLOTS").start()
        self.slots.load.return_value = (200, {"ok": True, "devices": [0], "place": "CUDA0"})
        self.slots.unload.return_value = (200, {"ok": True, "freed": {0: 4000}})
        self.router = mock.patch.object(routes, "router",
                                        return_value=(200, {"success": True})).start()
        mock.patch.object(routes.REGISTRY, "for_model",
                          return_value=routes.REGISTRY.get("llamacpp")).start()
        self.addCleanup(mock.patch.stopall)


class Load(Base):
    def test_the_dashboard_alias_goes_through_the_planner(self):
        status, out = routes.post_load(Req(body={"model": "m1"}))
        self.assertEqual(status, 200)
        self.assertTrue(out["success"])
        self.slots.load.assert_called_once_with("m1", "main", False, wait=True)
        self.router.assert_not_called()

    def test_a_refusal_reads_like_a_router_error(self):
        self.slots.load.return_value = (409, dict(VERDICT_NO))
        status, out = routes.post_load(Req(body={"model": "m1"}))
        self.assertEqual(status, 409)
        self.assertFalse(out["success"])
        self.assertIn("GiB", out["error"]["message"])
        self.assertEqual(out["evict"], ["w1"])

    def test_engine_agnostic_route_too(self):
        self.slots.load.return_value = (409, dict(VERDICT_NO))
        status, out = routes.post_model_load(Req(body={"model": "m1", "role": "worker",
                                                        "evict": True}))
        self.assertEqual((status, out["ok"], out["backend"]), (409, False, "llamacpp"))
        self.assertIn("GiB", out["error"])
        self.slots.load.assert_called_once_with("m1", "worker", True, wait=True)

    def test_role_is_checked(self):
        with self.assertRaises(routes.ApiError):
            routes.post_load(Req(body={"model": "m1", "role": "boss"}))

    def test_off_is_the_plain_path(self):
        self.cfg["multi_model"] = False
        routes.post_load(Req(body={"model": "m1"}))
        self.slots.load.assert_not_called()
        self.router.assert_called_once_with("/models/load", "POST", {"model": "m1"})

    def test_a_single_router_is_the_plain_path(self):
        # the runner's router (or one started before the setting) evicts by LRU
        # itself; the planner's numbers would be for a pool that isn't running
        self.pool.return_value = None
        routes.post_load(Req(body={"model": "m1"}))
        self.slots.load.assert_not_called()

    def test_ik_llama_is_the_plain_path(self):
        self.cfg["active_engine"] = "ikllama"
        routes.post_load(Req(body={"model": "m1"}))
        self.slots.load.assert_not_called()


class Unload(Base):
    def test_unload_is_measured(self):
        status, out = routes.post_unload(Req(body={"model": "m1"}))
        self.assertEqual(status, 200)
        self.assertTrue(out["success"])
        self.slots.unload.assert_called_once_with("m1")

    def test_unload_all(self):
        self.router.return_value = (200, {"data": [
            {"id": "m1", "status": {"value": "loaded"}},
            {"id": "m2", "status": {"value": "unloaded"}}]})
        status, out = routes.post_unload_all(Req())
        self.assertEqual(out["unloaded"], ["m1"])
        self.slots.unload.assert_called_once_with("m1")

    def test_off_is_the_plain_path(self):
        self.cfg["multi_model"] = False
        routes.post_model_unload(Req(body={"model": "m1"}))
        self.slots.unload.assert_not_called()


class Endpoints(Base):
    def test_state_of_the_slots(self):
        self.slots.loaded.return_value = [{"model": "m1", "role": "main"}]
        status, out = routes.get_slots(Req())
        self.assertTrue(out["enabled"])
        self.assertEqual(out["loaded"], [{"model": "m1", "role": "main"}])
        self.assertEqual(out["pool"], {"models_max": 3, "autoload": False})

    def test_off_reports_why(self):
        self.pool.return_value = None
        status, out = routes.get_slots(Req())
        self.assertFalse(out["enabled"])
        self.assertTrue(out["multi_model"])
        self.assertTrue(out["restart_needed"])

    def test_plan(self):
        self.slots.plan.return_value = dict(VERDICT_NO)
        status, out = routes.get_slots_plan(Req(qs={"model": "m1", "role": "main"}))
        self.assertEqual((status, out["ok"]), (200, False))
        self.slots.plan.assert_called_once_with("m1", "main")

    def test_plan_needs_a_model(self):
        with self.assertRaises(routes.ApiError):
            routes.get_slots_plan(Req(qs={}))

    def test_set_main(self):
        routes.post_slots_main(Req(body={"model": "m2"}))
        self.slots.set_main.assert_called_once_with("m2")

    def test_registered(self):
        self.assertIs(routes.GET_ROUTES["/api/slots"], routes.get_slots)
        self.assertIs(routes.GET_ROUTES["/api/slots/plan"], routes.get_slots_plan)
        self.assertIs(routes.POST_ROUTES["/api/slots/main"], routes.post_slots_main)
        self.assertIs(routes.POST_ROUTES["/api/slots/apply"], routes.post_slots_apply)


class StateSlots(Base):
    """What the dashboard's 4-second poll carries: roles and settings, never
    a planner run or an nvidia-smi call."""
    def test_roles_and_settings(self):
        self.cfg.update({"slots": {"main": "m1"}, "slot_cap": 4, "slot_headroom_mib": 2048})
        self.slots.devices.return_value = {"m1": [0], "w1": [1]}
        self.slots.footprints.return_value = {"m1": {"0": 9000}}
        out = routes._slots_state(dict(self.cfg))
        self.assertEqual((out["enabled"], out["main"], out["restart_needed"]), (True, "m1", False))
        self.assertEqual(out["devices"], {"m1": [0], "w1": [1]})
        self.assertEqual(out["footprints"], {"m1": {"0": 9000}})
        self.assertEqual(out["settings"], {"multi_model": True, "slot_cap": 4,
                                           "slot_headroom_mib": 2048, "slot_autoload": False})
        self.assertEqual(out["cap_range"], [routes.slots.CAP_MIN, routes.slots.CAP_MAX])
        self.slots.loaded.assert_not_called()
        self.slots.plan.assert_not_called()

    def test_a_single_router_needs_a_restart(self):
        self.pool.return_value = None
        out = routes._slots_state(dict(self.cfg))
        self.assertEqual((out["enabled"], out["restart_needed"], out["main"], out["devices"],
                          out["footprints"]), (False, True, "", {}, {}))

    def test_ik_llama_is_not_a_restart(self):
        self.cfg["active_engine"] = "ikllama"
        out = routes._slots_state(dict(self.cfg))
        self.assertEqual((out["enabled"], out["restart_needed"], out["engine_ok"]),
                         (False, False, False))

    def test_off(self):
        self.cfg["multi_model"] = False
        out = routes._slots_state(dict(self.cfg))
        self.assertEqual((out["enabled"], out["restart_needed"]), (False, False))
        self.assertFalse(out["settings"]["multi_model"])

    def test_junk_in_config_reads_as_defaults(self):
        self.cfg.update({"slots": "junk", "slot_cap": "lots"})
        out = routes._slots_state(dict(self.cfg))
        self.assertEqual((out["main"], out["settings"]["slot_cap"]), ("", 3))

    def test_in_the_state(self):
        with mock.patch.object(routes.REGISTRY, "state", return_value={"models": []}), \
             mock.patch.object(routes._GPU_TELEMETRY, "get", return_value=[]):
            status, out = routes.get_state(Req())
        self.assertTrue(out["slots"]["enabled"])


class Apply(Base):
    def test_restarts_the_router_with_the_pool(self):
        with mock.patch.object(routes, "_sync_router_pool", return_value=(True, "")) as sync:
            status, out = routes.post_slots_apply(Req(body={}))
        self.assertEqual((status, out), (200, {"ok": True, "restarted": True, "error": ""}))
        sync.assert_called_once()

    def test_a_failed_restart_says_why(self):
        with mock.patch.object(routes, "_sync_router_pool", return_value=(False, "port busy")):
            status, out = routes.post_slots_apply(Req(body={}))
        self.assertEqual((status, out["ok"], out["error"]), (500, False, "port busy"))


class TurningItOff(Base):
    def test_puts_the_ini_back(self):
        mock.patch.object(routes.config, "update",
                          side_effect=lambda ch: dict(self.cfg, **ch)).start()
        mock.patch.object(routes, "_sync_router_pool", return_value=(True, "")).start()
        routes.post_config(Req(body={"multi_model": False}))
        self.slots.unplace_all.assert_called_once_with()

    def test_on_leaves_it(self):
        mock.patch.object(routes.config, "update",
                          side_effect=lambda ch: dict(self.cfg, **ch)).start()
        mock.patch.object(routes, "_sync_router_pool", return_value=(True, "")).start()
        routes.post_config(Req(body={"multi_model": True}))
        self.slots.unplace_all.assert_not_called()

    def test_startup_with_it_off_cleans_up(self):
        self.cfg["multi_model"] = False
        self.pool.return_value = None
        routes.reconcile_router_pool()
        self.slots.unplace_all.assert_called_once_with()


class AutoLoad(Base):
    def setUp(self):
        super().setUp()
        import server
        self.server = server
        self.router.return_value = (200, {"data": [{"id": "m1", "status": {"value": "unloaded"}}]})

    def test_the_favourite_is_the_main_model(self):
        self.server._auto_load("m1")
        self.slots.load.assert_called_once_with("m1", "main", wait=False)
        self.assertNotIn(mock.call("/models/load", "POST", {"model": "m1"}),
                         self.router.call_args_list)

    def test_single_mode_unchanged(self):
        self.cfg["multi_model"] = False
        self.server._auto_load("m1")
        self.slots.load.assert_not_called()
        self.router.assert_any_call("/models/load", "POST", {"model": "m1"})


class EmbersPool(Base):
    """What the embers scheduler may do with the pool: worker loads only, never
    an eviction, never a role change."""

    def test_loads_are_workers_that_evict_nothing(self):
        routes.EmbersPool().load("q")
        self.slots.load.assert_called_once_with("q", "worker", False, wait=True, keep_role=True)

    def test_plans_are_a_workers(self):
        routes.EmbersPool().plan("q")
        self.slots.plan.assert_called_once_with("q", "worker")

    def test_active_only_on_a_running_pool(self):
        self.assertTrue(routes.EmbersPool().active())
        self.pool.return_value = None
        self.assertFalse(routes.EmbersPool().active())


if __name__ == "__main__":
    unittest.main()
