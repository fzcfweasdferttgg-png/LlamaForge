import conftest_paths  # noqa: F401
import io, json, time, unittest, urllib.error

from embers import llm

SCHEMA = {"type": "object", "required": ["ops"], "properties": {
    "ops": {"type": "array", "items": {"type": "object", "required": ["op"], "properties": {
        "op": {"type": "string", "enum": ["add", "close"]}, "n": {"type": "integer"}}}}}}


class CheckShapeTest(unittest.TestCase):
    def test_ok_and_errors(self):
        self.assertIsNone(llm.check_shape({"ops": [{"op": "add", "n": 1}]}, SCHEMA))
        self.assertEqual(llm.check_shape([], SCHEMA), "$ should be object")
        self.assertEqual(llm.check_shape({}, SCHEMA), "$.ops is missing")
        self.assertIn("one of", llm.check_shape({"ops": [{"op": "rm"}]}, SCHEMA))
        self.assertEqual(llm.check_shape({"ops": [{"op": "add", "n": True}]}, SCHEMA),
                         "$.ops[0].n should be integer")

    def test_deep_nesting_is_capped(self):
        schema = {"type": "array"}
        schema["items"] = schema  # self-referential: only the depth cap bounds it
        obj = []
        for _ in range(500):
            obj = [obj]
        self.assertIn("too deeply nested", llm.check_shape(obj, schema))

    def test_unknown_schema_type_does_not_crash(self):
        self.assertIsNone(llm.check_shape(1, {"type": "weird"}))


class ParseJsonTest(unittest.TestCase):
    def test_plain_and_fenced(self):
        self.assertEqual(llm.parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(llm.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            llm.parse_json("sure! here you go")

    def test_think_block_stripped(self):
        self.assertEqual(llm.parse_json('<think>hmm {"x": 2} maybe</think>\n{"a": 1}'), {"a": 1})
        self.assertEqual(llm.parse_json('<think>\nthinking\n</think>\n```json\n{"a": 1}\n```'), {"a": 1})

    def test_closing_tag_only_and_variants_take_the_final_answer(self):
        final = {"ops": ["final"]}
        for text in ('reasoning {"ops":["draft"]} done</think>\n{"ops":["final"]}',
                     '\ufeff<think>try {"ops":["draft"]}</think>{"ops":["final"]}',
                     '<Think>{"ops":["draft"]}</Think>{"ops":["final"]}',
                     '<think>a</think><think>{"ops":["draft"]}</think>{"ops":["final"]}'):
            self.assertEqual(llm.parse_json(text), final, msg=text)

    def test_unterminated_think_is_an_error(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json('<think>ran out of tokens {"a": 1}')

    def test_prose_around_object(self):
        self.assertEqual(llm.parse_json('Here: {"a": [1, 2]} hope that helps'), {"a": [1, 2]})

    def test_deep_nesting_raises_own_error(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json("[" * 100000 + "]" * 100000)

    def test_oversized_input_rejected(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json(" " * (llm.MAX_JSON_CHARS + 1) + "{}")

    def test_huge_whitespace_not_catastrophic(self):
        t = time.time()
        with self.assertRaises(llm.LLMError):
            llm.parse_json("```json" + " " * 1_000_000 + "x")
        self.assertLess(time.time() - t, 2)

    def test_none_input(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json(None)


class AskJsonTest(unittest.TestCase):
    def scripted(self, *texts):
        calls = []
        texts = list(texts)

        def complete(messages, schema, max_tokens):
            calls.append(messages)
            return texts.pop(0), {"prompt_tokens": 10, "completion_tokens": 5}
        return complete, calls

    def test_first_try(self):
        complete, calls = self.scripted('{"ops": []}')
        obj, usage = llm.ask_json(complete, [{"role": "user", "content": "x"}], SCHEMA)
        self.assertEqual(obj, {"ops": []})
        self.assertEqual(len(calls), 1)
        self.assertEqual(usage, {"prompt_tokens": 10, "completion_tokens": 5})

    def test_one_retry_with_hint(self):
        complete, calls = self.scripted("not json", '{"ops": []}')
        obj, usage = llm.ask_json(complete, [{"role": "user", "content": "x"}], SCHEMA)
        self.assertEqual(obj, {"ops": []})
        self.assertIn("rejected", calls[1][-1]["content"])
        self.assertEqual(usage["prompt_tokens"], 20)

    def test_gives_up_after_two(self):
        complete, calls = self.scripted('{"nope": 1}', '{"nope": 2}')
        with self.assertRaises(llm.LLMError):
            llm.ask_json(complete, [{"role": "user", "content": "x"}], SCHEMA)
        self.assertEqual(len(calls), 2)

    def test_deep_garbage_retries_then_llm_error(self):
        deep = "[" * 100000
        complete, calls = self.scripted(deep, deep)
        with self.assertRaises(llm.LLMError):
            llm.ask_json(complete, [{"role": "user", "content": "x"}], SCHEMA)
        self.assertEqual(len(calls), 2)

    def test_reasoning_reply_accepted(self):
        complete, calls = self.scripted('<think>plan</think>{"ops": []}')
        obj, _ = llm.ask_json(complete, [{"role": "user", "content": "x"}], SCHEMA)
        self.assertEqual(obj, {"ops": []})
        self.assertEqual(len(calls), 1)


class RouterTest(unittest.TestCase):
    def make(self, replies, cfg=None):
        self.sent = []

        def request(url, body=None, key="", timeout=None):
            self.sent.append((url, body, key))
            r = replies[url.split("?")[0].rsplit("/", 1)[-1]]
            if isinstance(r, Exception):
                raise r
            return r
        return llm.Router(cfg or {"router_port": 8080, "router_api_key": "k" * 32}, request=request)

    def test_loaded_model_and_n_ctx(self):
        r = self.make({"models": {"data": [{"id": "a", "status": {"value": "unloaded"}},
                                           {"id": "b", "status": {"value": "loaded"}}]},
                       "props": {"default_generation_settings": {"n_ctx": 32768}}})
        self.assertEqual(r.loaded_model(), "b")
        self.assertEqual(r.n_ctx("b"), 32768)
        self.assertEqual(self.sent[0], ("http://127.0.0.1:8080/v1/models", None, "k" * 32))
        self.assertTrue(self.sent[1][0].endswith("/props?model=b"))

    def test_n_ctx_falls_back(self):
        r = self.make({"props": llm.LLMError("404")})
        self.assertEqual(r.n_ctx("b"), llm.DEFAULT_N_CTX)

    def test_llm_call_shape(self):
        r = self.make({"completions": {"choices": [{"message": {"content": '{"ops": []}'}}],
                                       "usage": {"prompt_tokens": 7, "completion_tokens": 3}}})
        call = r.llm("b", max_tokens_cap=1000)
        obj, usage = call([{"role": "user", "content": "x"}], SCHEMA, 4096)
        self.assertEqual(obj, {"ops": []})
        url, body, key = self.sent[0]
        self.assertTrue(url.endswith("/v1/chat/completions"))
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual((body["model"], body["temperature"], body["max_tokens"]), ("b", 0.2, 1000))
        # A reasoning model would spend max_tokens thinking and never write the JSON
        # (seen on a live 27B with reasoning-effort xhigh); templates ignore unknown kwargs.
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_missing_message(self):
        r = self.make({"completions": {"choices": []}})
        with self.assertRaises(llm.LLMError):
            r.llm("b")([{"role": "user", "content": "x"}], SCHEMA)

    def test_malformed_replies_become_llm_error(self):
        for bad in ({"choices": [{"message": {"content": None}}]},
                    {"choices": [{"message": None}]},
                    {"choices": [None]},
                    {"choices": "x"},
                    {"choices": [{}]},
                    {"choices": [{"message": {"content": 5}}]},
                    [], "str", None):
            r = self.make({"completions": bad})
            with self.assertRaises(llm.LLMError, msg=repr(bad)):
                r.complete("b")([{"role": "user", "content": "x"}], SCHEMA, 100)

    def test_models_malformed_is_none_or_llm_error(self):
        for bad in ([], {"data": "x"}, {"data": [None, 3]}, None):
            r = self.make({"models": bad})
            try:
                self.assertIsNone(r.loaded_model())
            except llm.LLMError:
                pass

    def test_n_ctx_malformed_props(self):
        r = self.make({"props": []})
        self.assertEqual(r.n_ctx("b"), llm.DEFAULT_N_CTX)

    def test_port_validation(self):
        for bad in (0, 65536, -1, "8080; evil", "evil.com", None, True, 80.5, "", [8080]):
            with self.assertRaises(llm.LLMError, msg=repr(bad)):
                llm.Router({"router_port": bad})
        with self.assertRaises(llm.LLMError):
            llm.Router({})
        self.assertEqual(llm.Router({"router_port": "9090"}).base, "http://127.0.0.1:9090")

    def test_host_in_config_is_ignored(self):
        r = llm.Router({"router_port": 8080, "router_host": "evil.example"})
        self.assertEqual(r.base, "http://127.0.0.1:8080")

    def test_key_fallback_to_local_key(self):
        r = self.make({"models": {"data": []}}, {"router_port": 8080, "router_local_key": "L" * 8})
        r.loaded_model()
        self.assertEqual(self.sent[0][2], "L" * 8)

    def test_key_never_in_repr(self):
        r = llm.Router({"router_port": 8080, "router_api_key": "SECRETKEY123"})
        self.assertNotIn("SECRETKEY123", repr(r))
        self.assertNotIn("SECRETKEY123", str(r))


class _FakeResp:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RequestTest(unittest.TestCase):
    def run_with(self, fake, **kw):
        orig = llm._open
        llm._open = fake
        try:
            return llm._request("http://127.0.0.1:1/x", **kw)
        finally:
            llm._open = orig

    def test_timeout_always_passed_and_auth_header(self):
        seen = {}

        def fake(req, timeout):
            seen["timeout"] = timeout
            seen["auth"] = req.get_header("Authorization")
            return _FakeResp(b'{"ok": 1}')
        self.assertEqual(self.run_with(fake, key="abc"), {"ok": 1})
        self.assertTrue(seen["timeout"] and seen["timeout"] > 0)
        self.assertEqual(seen["auth"], "Bearer abc")

    def test_response_size_capped(self):
        big = b'{"a": "' + b"x" * (llm.MAX_RESPONSE_BYTES + 10) + b'"}'
        with self.assertRaises(llm.LLMError):
            self.run_with(lambda req, timeout: _FakeResp(big))

    def test_invalid_utf8_and_deep_json_are_llm_error(self):
        with self.assertRaises(llm.LLMError):
            self.run_with(lambda req, timeout: _FakeResp(b"\xff\xfe not json"))
        with self.assertRaises(llm.LLMError):
            self.run_with(lambda req, timeout: _FakeResp(b"[" * 100000))

    def test_http_error_and_key_redaction(self):
        def fake(req, timeout):
            raise urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"bad key SECRETKEY123"))
        with self.assertRaises(llm.LLMError) as cm:
            self.run_with(fake, key="SECRETKEY123")
        self.assertIn("401", str(cm.exception))
        self.assertNotIn("SECRETKEY123", str(cm.exception))

    def test_connection_error_redacts_key(self):
        def fake(req, timeout):
            raise OSError("connect failed SECRETKEY123")
        with self.assertRaises(llm.LLMError) as cm:
            self.run_with(fake, key="SECRETKEY123")
        self.assertNotIn("SECRETKEY123", str(cm.exception))

    def test_outages_are_router_unavailable_and_other_failures_are_not(self):
        def raiser(exc):
            def fake(req, timeout):
                raise exc
            return fake
        outages = [ConnectionRefusedError("refused"),
                   urllib.error.URLError(ConnectionRefusedError(10061, "refused")),
                   urllib.error.HTTPError("u", 503, "Loading model", {}, io.BytesIO(b"loading"))]
        for exc in outages:
            with self.assertRaises(llm.RouterUnavailable, msg=repr(exc)):
                self.run_with(raiser(exc))
        others = [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out")),
                  urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"context too long")),
                  urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"boom"))]
        for exc in others:
            with self.assertRaises(llm.LLMError) as cm:
                self.run_with(raiser(exc))
            self.assertNotIsInstance(cm.exception, llm.RouterUnavailable, repr(exc))
        with self.assertRaises(llm.LLMError) as cm:
            self.run_with(lambda req, timeout: _FakeResp(b"\xff\xfe not json"))
        self.assertNotIsInstance(cm.exception, llm.RouterUnavailable)

    def test_redirects_not_followed(self):
        self.assertIsNone(llm._NoRedirect().redirect_request(None, None, 302, "x", {}, "http://evil/"))


class LoopbackTruncationTest(unittest.TestCase):
    def test_truncated_chunked_body_is_llm_error(self):
        import http.server, socketserver, threading

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"64\r\n" + b'{"choices": [')  # promises 100 bytes, sends 13
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with self.assertRaises(llm.LLMError):
            llm._request(f"http://127.0.0.1:{srv.server_address[1]}/x", timeout=5)


if __name__ == "__main__":
    unittest.main()
