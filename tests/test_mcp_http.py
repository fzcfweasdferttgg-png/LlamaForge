"""MCP over HTTP (opt-in mcp_host): the stateless Streamable HTTP adapter.

Drives a real ThreadingHTTPServer the way an MCP client would - one JSON-RPC
message per POST - and checks the same guard the panel's surfaces use."""
import conftest_paths  # noqa: F401
import json, threading, unittest, urllib.error, urllib.request

import mcp_server


def rpc(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg).encode()


class LiveMcpHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = mcp_server.serve_http(lambda: {"mcp_host": "127.0.0.1",
                                                   "mcp_port": 0})
        if cls.httpd is None:
            raise AssertionError("serve_http did not start")
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, body, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp",
                                     data=body, headers=h)
        return urllib.request.urlopen(req, timeout=5)

    def test_initialize_roundtrip(self):
        with self._post(rpc("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"}})) as r:
            self.assertEqual(r.status, 200)
            out = json.loads(r.read())
        self.assertEqual(out["id"], 1)
        self.assertIn("serverInfo", out["result"])

    def test_tools_list(self):
        with self._post(rpc("tools/list", id_=2)) as r:
            names = [t["name"] for t in json.loads(r.read())["result"]["tools"]]
        self.assertIn("status", names)
        self.assertIn("load_model", names)

    def test_notification_gets_accepted_without_body(self):
        body = json.dumps({"jsonrpc": "2.0",
                           "method": "notifications/initialized"}).encode()
        with self._post(body) as r:
            self.assertEqual(r.status, 202)
            self.assertEqual(r.read(), b"")

    def test_foreign_host_refused(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp", data=rpc("tools/list"),
            headers={"Content-Type": "application/json",
                     "Host": f"evil.com:{self.port}"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_non_json_content_type_refused(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp", data=b"a=1",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 415)

    def test_get_is_method_not_allowed(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 405)


class DisabledByDefaultTest(unittest.TestCase):
    def test_empty_mcp_host_starts_nothing(self):
        self.assertIsNone(mcp_server.serve_http(lambda: {"mcp_host": ""}))


if __name__ == "__main__":
    unittest.main()
