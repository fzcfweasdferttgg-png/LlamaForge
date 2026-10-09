"""LAN exposure: the panel leaving loopback, and the keyless-LAN
opt-in for the router.

The default install is unchanged - everything binds 127.0.0.1 and a LAN
router needs a key. These tests drive a real ThreadingHTTPServer the way a LAN
client would once panel_host says 0.0.0.0."""
import conftest_paths  # noqa: F401
import json, os, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import network_policy, routes, server


class LanHostsTest(unittest.TestCase):
    def test_local_or_empty_stays_loopback_only(self):
        self.assertEqual(server.lan_hosts("127.0.0.1"), set())
        self.assertEqual(server.lan_hosts(""), set())

    def test_fixed_bind_yields_that_address(self):
        self.assertEqual(server.lan_hosts("10.0.0.5"), {"10.0.0.5"})

    def test_all_interfaces_yields_local_names(self):
        out = server.lan_hosts("0.0.0.0")
        self.assertTrue(out)
        self.assertNotIn("", out)
        self.assertNotIn("0.0.0.0", out)


class GuardWideningTest(unittest.TestCase):
    def setUp(self):
        self.saved = server.EXTRA_HOSTS.copy()
        server.EXTRA_HOSTS.clear()
        server.EXTRA_HOSTS.add("10.0.0.5")
        self.addCleanup(self._restore)

    def _restore(self):
        server.EXTRA_HOSTS.clear()
        server.EXTRA_HOSTS |= self.saved

    def test_panel_host_accepts_the_configured_address(self):
        self.assertTrue(server._host_ok("10.0.0.5:8090", 8090))
        self.assertTrue(server._origin_ok("http://10.0.0.5:8090", 8090))

    def test_panel_host_still_rejects_foreign_names(self):
        self.assertFalse(server._host_ok("evil.com:8090", 8090))
        self.assertFalse(server._host_ok("attacker.test:8090", 8090))
        self.assertFalse(server._host_ok("10.0.0.5:9999", 8090))


class KeylessLanPolicyTest(unittest.TestCase):
    def test_default_still_fails_closed(self):
        result = network_policy.assess("0.0.0.0", "")
        self.assertFalse(result.start_allowed)
        self.assertEqual(result.configured_security_status, "unsafe_legacy")

    def test_opt_in_allows_keyless_lan(self):
        result = network_policy.assess("0.0.0.0", "", allow_keyless_lan=True)
        self.assertTrue(result.start_allowed)
        self.assertEqual(result.configured_security_status, "lan_open")
        self.assertEqual(result.access_scope, "lan")

    def test_opt_in_does_not_touch_local_or_keyed(self):
        self.assertTrue(network_policy.assess("127.0.0.1", "").start_allowed)
        keyed = network_policy.assess("0.0.0.0", "x" * 32)
        self.assertTrue(keyed.start_allowed)
        self.assertEqual(keyed.configured_security_status, "protected")

    def test_start_error_passes_the_flag(self):
        self.assertTrue(network_policy.start_error("0.0.0.0", ""))
        self.assertFalse(network_policy.start_error("0.0.0.0", "",
                                                    allow_keyless_lan=True))

    def test_preflight_reads_the_flag_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"router_host": "0.0.0.0",
                           "router_allow_keyless_lan": True}, f)
            ok, reason = network_policy.preflight_config_file(path)
            self.assertTrue(ok, reason)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"router_host": "0.0.0.0"}, f)
            ok, reason = network_policy.preflight_config_file(path)
            self.assertFalse(ok)


class LiveLanServerTest(unittest.TestCase):
    """A LAN-bound panel answering real requests from a non-loopback name."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.cfg_patch = mock.patch.object(
            routes, "cfg", return_value={"panel_port": self.port,
                                         "anthropic_shim_enabled": False})
        self.cfg_patch.start()
        self.addCleanup(self.cfg_patch.stop)

        def probe(req):
            return 200, {"ok": True}

        self.routes_patch = mock.patch.dict(
            routes.GET_ROUTES, {"/api/_probe": probe}, clear=False)
        self.routes_patch.start()
        self.addCleanup(self.routes_patch.stop)
        self.saved = server.EXTRA_HOSTS.copy()
        server.EXTRA_HOSTS.clear()
        server.EXTRA_HOSTS.add("10.0.0.5")
        self.addCleanup(self._restore)

    def _restore(self):
        server.EXTRA_HOSTS.clear()
        server.EXTRA_HOSTS |= self.saved

    def _req(self, host):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/_probe",
            headers={"Host": host})
        return urllib.request.urlopen(req, timeout=5)

    def test_request_with_lan_host_header_is_served(self):
        with self._req(f"10.0.0.5:{self.port}") as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read())["ok"])

    def test_request_with_foreign_host_header_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._req(f"evil.com:{self.port}")
        self.assertEqual(cm.exception.code, 403)


class KeylessRunnerArgsTest(unittest.TestCase):
    """The runner CLI path (run.sh / run.ps1) honors the keyless opt-in: no
    --api-key argv, and no router_local_key minted into the config."""

    def _write(self, td, data):
        path = os.path.join(td, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_keyless_lan_helper_needs_all_three(self):
        base = {"router_host": "0.0.0.0", "router_api_key": "",
                "router_allow_keyless_lan": True}
        self.assertTrue(network_policy.keyless_lan(base))
        self.assertFalse(network_policy.keyless_lan({**base, "router_api_key": "x"}))
        self.assertFalse(network_policy.keyless_lan({**base, "router_host": "127.0.0.1"}))
        self.assertFalse(network_policy.keyless_lan(
            {**base, "router_allow_keyless_lan": False}))

    def test_router_args_are_empty_and_config_is_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write(td, {"router_host": "0.0.0.0",
                                   "router_api_key": "",
                                   "router_allow_keyless_lan": True})
            ok, args = network_policy.router_args_for_config_file(path, "missing-bin")
            self.assertTrue(ok)
            self.assertEqual(args, [])
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertNotIn("router_local_key", cfg)

    def test_local_still_mints_and_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write(td, {"router_host": "127.0.0.1",
                                   "router_api_key": ""})
            ok, args = network_policy.router_args_for_config_file(path, "missing-bin")
            self.assertTrue(ok)
            self.assertIn("--api-key", args)


if __name__ == "__main__":
    unittest.main()
