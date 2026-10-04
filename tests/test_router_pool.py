import conftest_paths  # noqa: F401
import unittest
from unittest import mock

import router_ctl
import slots


def spawn(pool=None):
    with mock.patch.object(router_ctl.os.path, "exists", return_value=True), \
         mock.patch.object(router_ctl, "is_running", return_value=False), \
         mock.patch.object(router_ctl, "supports_cors_origins", return_value=True), \
         mock.patch.object(router_ctl.os, "makedirs"), \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(router_ctl.procs, "write_pid"), \
         mock.patch.object(router_ctl.subprocess, "Popen") as popen:
        ok, err = router_ctl.start("server", "models.ini", 8080, "127.0.0.1", "", "logs",
                                   "L" * 43, pool)
    assert ok, err
    return popen.call_args


class Start(unittest.TestCase):
    def test_single_mode_is_unchanged(self):
        argv = spawn().args[0]
        self.assertEqual(argv[argv.index("--models-max") + 1], "1")
        self.assertNotIn("--no-models-autoload", argv)

    def test_pool_raises_the_cap_and_stops_autoload(self):
        argv = spawn({"models_max": 3, "autoload": False}).args[0]
        self.assertEqual(argv[argv.index("--models-max") + 1], "3")
        self.assertIn("--no-models-autoload", argv)

    def test_pool_may_keep_autoload(self):
        argv = spawn({"models_max": 2, "autoload": True}).args[0]
        self.assertNotIn("--no-models-autoload", argv)

    def test_device_order_is_left_alone(self):
        # forcing CUDA_DEVICE_ORDER would re-aim every device / main-gpu pin
        call = spawn({"models_max": 3, "autoload": False})
        env = call.kwargs.get("env")
        self.assertTrue(env is None or "CUDA_DEVICE_ORDER" not in env
                        or env["CUDA_DEVICE_ORDER"] == router_ctl.os.environ.get("CUDA_DEVICE_ORDER"))

    def test_restart_passes_the_pool_on(self):
        seen = []
        with mock.patch.object(router_ctl.network_policy, "start_error", return_value=""), \
             mock.patch.object(router_ctl, "stop", return_value=True), \
             mock.patch.object(router_ctl, "start",
                               side_effect=lambda *a: seen.append(a) or (True, "")):
            router_ctl.restart("server", "models.ini", 8080, "127.0.0.1", "", "logs", "",
                               {"models_max": 3, "autoload": False})
        self.assertEqual(seen[0][-1], {"models_max": 3, "autoload": False})


class RouterPool(unittest.TestCase):
    def test_off_is_none(self):
        self.assertIsNone(slots.router_pool({"multi_model": False}, True))
        self.assertIsNone(slots.router_pool({}, True))

    def test_on(self):
        self.assertEqual(slots.router_pool({"multi_model": True, "slot_cap": 3}, True),
                         {"models_max": 3, "autoload": False})

    def test_cap_is_clamped(self):
        self.assertEqual(slots.router_pool({"multi_model": True, "slot_cap": 9}, True)["models_max"], 4)
        self.assertEqual(slots.router_pool({"multi_model": True, "slot_cap": 1}, True)["models_max"], 2)
        self.assertEqual(slots.router_pool({"multi_model": True, "slot_cap": "x"}, True)["models_max"], 3)

    def test_autoload_opt_in(self):
        self.assertTrue(slots.router_pool({"multi_model": True, "slot_autoload": True},
                                          True)["autoload"])

    def test_a_build_that_cannot_stop_autoload_stays_single(self):
        # without --no-models-autoload any client request could load a model
        # behind the planner's back and the router would evict by LRU
        self.assertIsNone(slots.router_pool({"multi_model": True}, False))
        self.assertEqual(slots.router_pool({"multi_model": True, "slot_autoload": True}, False),
                         {"models_max": 3, "autoload": True})


if __name__ == "__main__":
    unittest.main()
