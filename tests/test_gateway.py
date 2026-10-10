import conftest_paths  # noqa: F401
import os, socket, stat, sys, tempfile, textwrap, unittest

import config, gateway, routes


CFG = {
    "router_port": 8080,
    "router_local_key": "L" * 43,
    "gateway_models": {"mix": ["q6-k", "rvn-q6-k"]},
    "gateway_preset": "default",
    "gateway_presets": {"default": {"router": {"routing_strategy": "simple-shuffle"}}},
}


class GatewayConfigTest(unittest.TestCase):
    """gateway.render_config: one deployment per (name, model) on the local
    router, and the active preset's sections copied through verbatim."""

    def text(self, **over):
        return gateway.render_config(dict(CFG, **over), "root")

    def test_one_deployment_per_model_under_one_name(self):
        t = self.text()
        self.assertEqual(t.count('model_name: "mix"'), 2)
        self.assertIn('model: "openai/q6-k"', t)
        self.assertIn('model: "openai/rvn-q6-k"', t)
        self.assertIn('api_base: "http://127.0.0.1:8080/v1"', t)
        self.assertIn('api_key: "%s"' % CFG["router_local_key"], t)

    def test_the_preset_rides_through_verbatim(self):
        t = self.text(gateway_preset="hot", gateway_presets={
            "default": {"router": {"routing_strategy": "simple-shuffle"}},
            "hot": {"router": {"routing_strategy": "latency-based-routing",
                               "num_retries": 5},
                    "litellm_settings": {"request_timeout": 600,
                                         "drop_params": True}},
        })
        self.assertIn('routing_strategy: "latency-based-routing"', t)
        self.assertIn("num_retries: 5", t)
        self.assertIn("litellm_settings:", t)
        self.assertIn("request_timeout: 600", t)
        self.assertIn("drop_params: true", t)

    def test_an_unknown_preset_falls_back_to_the_default(self):
        t = self.text(gateway_preset="ghost")
        self.assertIn('routing_strategy: "simple-shuffle"', t)
        self.assertNotIn("latency", t)

    def test_no_names_no_deployments(self):
        self.assertNotIn("model_name:", self.text(gateway_models={}))

    def test_the_router_key_wins_over_the_local_one(self):
        t = self.text(router_api_key="U" * 43)
        self.assertIn('api_key: "%s"' % ("U" * 43), t)
        self.assertNotIn("L" * 43, t)

    def test_the_router_key_becomes_the_master_key(self):
        t = self.text()
        self.assertIn('  master_key: "%s"' % CFG["router_local_key"], t)

    def test_a_preset_master_key_is_left_alone(self):
        t = self.text(gateway_presets={"default": {
            "general_settings": {"master_key": "sk-own"}}})
        self.assertIn('  master_key: "sk-own"', t)
        self.assertNotIn('  master_key: "%s"' % CFG["router_local_key"], t)

    def test_a_keyless_setup_permits_an_unset_master_key(self):
        permit = "LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY"
        keyless = {k: v for k, v in CFG.items() if k != "router_local_key"}
        self.assertEqual(gateway._serve_env(keyless).get(permit), "true")
        self.assertNotIn(permit, gateway._serve_env(CFG))
        keyed = dict(keyless, gateway_presets={"default": {
            "general_settings": {"master_key": "sk-own"}}})
        self.assertNotIn(permit, gateway._serve_env(keyed))

    def test_a_reserve_name_is_ordered_and_capped(self):
        t = self.text(gateway_models={"mix": {
            "mode": "reserve", "models": ["q6-k", "rvn-q6-k"], "when_busy": "queue"}})
        self.assertIn("order: 1", t)
        self.assertIn("order: 2", t)
        # the primary is capped to its slots; the last model keeps queueing
        self.assertEqual(t.count("max_parallel_requests"), 1)

    def test_when_busy_reject_caps_every_model(self):
        t = self.text(gateway_models={"mix": {
            "mode": "reserve", "models": ["q6-k", "rvn-q6-k"], "when_busy": "reject"}})
        self.assertEqual(t.count("max_parallel_requests"), 2)

    def test_the_cap_follows_parallel_from_models_ini(self):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "models.ini"), "w", encoding="utf-8") as f:
            f.write("[q6-k]\nparallel = 2\n[rvn-q6-k]\nparallel = 1\n")
        t = gateway.render_config(dict(CFG, gateway_models={"mix": {
            "mode": "reserve", "models": ["q6-k", "rvn-q6-k"]}}), root)
        self.assertIn("max_parallel_requests: 2", t)
        self.assertEqual(t.count("max_parallel_requests"), 1)

    def test_an_old_model_list_still_means_alternation(self):
        self.assertEqual(gateway.name_spec(["a", "b"]), {
            "mode": "alternation", "models": ["a", "b"], "when_busy": "queue"})
        self.assertNotIn("order:", self.text())


class GatewayProcessTest(unittest.TestCase):
    """start()/stop() against a stub litellm that serves the health URL - a
    real process and a real HTTP round trip, the way the panel drives it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        os.makedirs(os.path.join(self.root, "tools", "litellm", "venv", "bin"))
        # the "installed" check reads the venv python - a stub venv needs one
        open(os.path.join(self.root, "tools", "litellm", "venv", "bin", "python3"), "w").close()
        stub = os.path.join(self.root, "tools", "litellm", "venv", "bin", "litellm")
        with open(stub, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(f"""\
                #!{sys.executable}
                import http.server, sys
                port = int(sys.argv[sys.argv.index("--port") + 1])

                class H(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"ok")

                    def log_message(self, *a):
                        pass

                http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
                """))
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        self.cfg = {"gateway_port": self.port, "gateway_bind": "",
                    "gateway_models": {"mix": ["a"]},
                    "gateway_preset": "default",
                    "gateway_presets": CFG["gateway_presets"]}
        self.addCleanup(gateway.stop, self.root)

    def test_start_serves_and_stop_frees_the_port(self):
        ok, error = gateway.start(self.cfg, self.root)
        self.assertTrue(ok, error)
        self.assertTrue(gateway.is_running(self.root))
        self.assertTrue(gateway.status(self.cfg, self.root)["responding"])
        self.assertTrue(gateway.stop(self.root))
        self.assertFalse(gateway.is_running(self.root))

    def test_start_without_names_is_refused_before_any_spawn(self):
        ok, error = gateway.start(dict(self.cfg, gateway_models={}), self.root)
        self.assertFalse(ok)
        self.assertIn("no virtual model", error)
        self.assertFalse(gateway.is_running(self.root))

    def test_starting_twice_keeps_the_one_process(self):
        ok, error = gateway.start(self.cfg, self.root)
        self.assertTrue(ok, error)
        self.assertEqual(gateway.start(self.cfg, self.root), (True, ""))
        self.assertTrue(gateway.is_running(self.root))


class GatewayRoutesTest(unittest.TestCase):
    """routes.post_gateway_save takes a partial body: omitted fields keep
    their current value. Handlers are called directly, no socket."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.dir, "config.json")
        self.addCleanup(setattr, config, "CONFIG", self._orig)

    def test_a_partial_save_keeps_the_other_fields(self):
        status, _ = routes.post_gateway_save(routes.Req(body={
            "enabled": True, "port": 8301, "bind": "0.0.0.0",
            "models": {"mix": ["a", " b "]}}))
        self.assertEqual(status, 200)
        status, _ = routes.post_gateway_save(routes.Req(body={"preset": "default"}))
        self.assertEqual(status, 200)
        c = config.load()
        self.assertTrue(c["gateway_enabled"])
        self.assertEqual(c["gateway_port"], 8301)
        self.assertEqual(c["gateway_bind"], "0.0.0.0")
        self.assertEqual(c["gateway_models"], {"mix": {
            "mode": "alternation", "models": ["a", "b"], "when_busy": "queue"}})

    def test_a_reserve_spec_round_trips_and_validates(self):
        status, _ = routes.post_gateway_save(routes.Req(body={"models": {"mix": {
            "mode": "reserve", "models": ["a", "b"], "when_busy": "reject"}}}))
        self.assertEqual(status, 200)
        self.assertEqual(config.load()["gateway_models"]["mix"], {
            "mode": "reserve", "models": ["a", "b"], "when_busy": "reject"})
        with self.assertRaises(routes.ApiError):
            routes.post_gateway_save(routes.Req(body={"models": {"mix": {
                "mode": "reserve", "models": ["a"], "when_busy": "explode"}}}))
        with self.assertRaises(routes.ApiError):
            routes.post_gateway_save(routes.Req(body={"models": {"mix": {
                "mode": "chaos", "models": ["a"]}}}))

    def test_a_bad_port_is_rejected(self):
        with self.assertRaises(routes.ApiError):
            routes.post_gateway_save(routes.Req(body={"port": 0}))


if __name__ == "__main__":
    unittest.main()
