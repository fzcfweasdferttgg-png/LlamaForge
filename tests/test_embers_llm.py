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


def _no_network(req, timeout):
    raise AssertionError(f"network: test tried to reach {req.full_url}")


class _NoNetwork(unittest.TestCase):
    """Router tests must inject fake transports; a missed injection fails loudly
    here instead of reaching the user's live router."""
    def setUp(self):
        orig = llm._open
        llm._open = _no_network
        self.addCleanup(setattr, llm, "_open", orig)


class RouterTest(_NoNetwork):
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


class RequestTextTest(unittest.TestCase):
    def run_with(self, fake, **kw):
        orig = llm._open
        llm._open = fake
        try:
            return llm._request_text("http://127.0.0.1:1/metrics", **kw)
        finally:
            llm._open = orig

    def test_returns_text_and_replaces_bad_utf8(self):
        self.assertEqual(self.run_with(lambda req, timeout: _FakeResp(b"a 1\n")), "a 1\n")
        self.assertEqual(self.run_with(lambda req, timeout: _FakeResp(b"x\xffy")), "x�y")

    def test_timeout_get_and_auth_header(self):
        seen = {}

        def fake(req, timeout):
            seen.update(timeout=timeout, auth=req.get_header("Authorization"), method=req.get_method())
            return _FakeResp(b"ok")
        self.run_with(fake, key="abc", timeout=7)
        self.assertEqual(seen, {"timeout": 7, "auth": "Bearer abc", "method": "GET"})

    def test_size_capped(self):
        big = b"x" * (llm.MAX_RESPONSE_BYTES + 10)
        with self.assertRaises(llm.LLMError):
            self.run_with(lambda req, timeout: _FakeResp(big))

    def test_key_scrubbed_and_error_mapping_shared(self):
        def fake(req, timeout):
            raise urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"bad key SECRETKEY123"))
        with self.assertRaises(llm.LLMError) as cm:
            self.run_with(fake, key="SECRETKEY123")
        self.assertIn("401", str(cm.exception))
        self.assertNotIn("SECRETKEY123", str(cm.exception))

        def oserr(req, timeout):
            raise OSError("connect failed SECRETKEY123")
        with self.assertRaises(llm.LLMError) as cm:
            self.run_with(oserr, key="SECRETKEY123")
        self.assertNotIn("SECRETKEY123", str(cm.exception))
        self.assertNotIsInstance(cm.exception, llm.RouterUnavailable)

        def refused(req, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        with self.assertRaises(llm.RouterUnavailable):
            self.run_with(refused)

        def loading(req, timeout):
            raise urllib.error.HTTPError("u", 503, "Loading", {}, io.BytesIO(b"loading"))
        with self.assertRaises(llm.RouterUnavailable):
            self.run_with(loading)

    def test_fetch_returns_raw_bytes(self):
        orig = llm._open
        llm._open = lambda req, timeout: _FakeResp(b"\x00\xffraw")
        try:
            self.assertEqual(llm._fetch("http://127.0.0.1:1/x", None, "", 5), b"\x00\xffraw")
        finally:
            llm._open = orig


KEY = "k" * 32
CFG = {"router_port": 8080, "router_api_key": KEY}


class RouterControlTest(_NoNetwork):
    def test_guard_blocks_real_transport(self):
        with self.assertRaises(AssertionError):
            llm.Router(CFG).models()

    _UNSET = AssertionError("transport not expected in this test")

    def router(self, request=_UNSET, request_text=_UNSET):
        # Both transports are always fakes (a canned reply may legitimately be None),
        # so a test can never reach a real router.
        self.sent = []

        def rec(fn):
            def call(url, body=None, key="", timeout=None):
                self.sent.append({"url": url, "body": body, "key": key, "timeout": timeout})
                r = fn(url) if callable(fn) else fn
                if isinstance(r, BaseException):
                    raise r
                return r
            return call
        return llm.Router(CFG, request=rec(request), request_text=rec(request_text))

    # models()
    def test_models_mixed_and_junk(self):
        r = self.router(request={"data": [
            {"id": "a", "status": {"value": "loaded"}},
            {"id": "b", "status": "unloaded"},
            {"id": "c", "status": {"value": "loading", "args": []}},
            {"id": "d"},
            {"id": "e", "status": {"value": "weird-new-state"}},
            {"id": "f", "status": {"nope": 1}},
            {"id": "g", "status": 3},
            None, 5, "x", ["id"], {"id": 7, "status": "loaded"}, {"status": "loaded"},
        ]})
        self.assertEqual(r.models(), [
            {"id": "a", "status": "loaded"}, {"id": "b", "status": "unloaded"},
            {"id": "c", "status": "loading"}, {"id": "d", "status": ""},
            {"id": "e", "status": "weird-new-state"}, {"id": "f", "status": ""},
            {"id": "g", "status": "3"}])
        self.assertEqual(self.sent[0]["url"], "http://127.0.0.1:8080/v1/models")
        self.assertEqual(self.sent[0]["key"], KEY)

    def test_models_malformed_top_level(self):
        for bad in ([], {"data": "x"}, None, "str", {"data": {"id": "a"}}):
            self.assertEqual(self.router(request=bad).models(), [], msg=repr(bad))

    # activity()
    def test_activity_plain(self):
        text = ("# HELP llamacpp:requests_processing Number of requests processing.\n"
                "# TYPE llamacpp:requests_processing gauge\n"
                "llamacpp:requests_processing 1\n"
                "# TYPE llamacpp:requests_deferred gauge\n"
                "llamacpp:requests_deferred 0\n"
                "llamacpp:prompt_tokens_total 1234\n")
        r = self.router(request_text=text)
        self.assertEqual(r.activity("m"), 1)
        self.assertEqual(self.sent[0]["url"], "http://127.0.0.1:8080/metrics?model=m")
        self.assertIsNone(self.sent[0]["body"])
        self.assertEqual(self.sent[0]["key"], KEY)

    def test_activity_labels_floats_and_sum(self):
        text = ('llamacpp:requests_processing{model="x"} 2.0\r\n'
                'llamacpp:requests_deferred{model="x",slot="0"}   3\n')
        self.assertEqual(self.router(request_text=text).activity("x"), 5)

    def test_activity_ignores_prefixed_unrelated_metrics(self):
        text = ("llamacpp:requests_processing_total 99\n"
                "llamacpp:requests_deferred_seconds 42\n"
                "xllamacpp:requests_processing 7\n"
                "llamacpp:requests_processing 0\n"
                "llamacpp:requests_deferred 0\n")
        self.assertEqual(self.router(request_text=text).activity("m"), 0)

    def test_activity_one_counter_is_enough(self):
        self.assertEqual(self.router(request_text="llamacpp:requests_processing 4\n").activity("m"), 4)

    def test_activity_missing_counters_is_error(self):
        for text in ("", "# only comments\n", "llamacpp:requests_processing_total 3\n",
                     "llamacpp:prompt_tokens_total 1\n"):
            with self.assertRaises(llm.LLMError, msg=repr(text)) as cm:
                self.router(request_text=text).activity("m")
            self.assertIn("missing request counters", str(cm.exception))

    def test_activity_non_finite_or_negative_is_error(self):
        for text in ("llamacpp:requests_processing NaN\n", "llamacpp:requests_processing +Inf\n",
                     "llamacpp:requests_deferred -1\n", "llamacpp:requests_processing 1e400\n"):
            with self.assertRaises(llm.LLMError, msg=repr(text)):
                self.router(request_text=text).activity("m")

    def test_activity_model_is_quoted(self):
        r = self.router(request_text="llamacpp:requests_processing 0\n")
        r.activity("org/model a&b=c?#")
        self.assertEqual(self.sent[0]["url"],
                         "http://127.0.0.1:8080/metrics?model=org%2Fmodel%20a%26b%3Dc%3F%23")

    def test_activity_oversized_text_is_error(self):
        big = "llamacpp:requests_processing 0\n" + "#" * (llm.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(llm.LLMError):
            self.router(request_text=big).activity("m")

    def test_activity_non_str_reply_is_error(self):
        for bad in (None, b"llamacpp:requests_processing 0", 3):
            with self.assertRaises(llm.LLMError, msg=repr(bad)):
                self.router(request_text=bad).activity("m")

    def test_activity_propagates_router_unavailable(self):
        with self.assertRaises(llm.RouterUnavailable):
            self.router(request_text=llm.RouterUnavailable("down")).activity("m")

    # load() / unload()
    def test_load_and_unload(self):
        r = self.router(request={"success": True})
        self.assertIsNone(r.load("org/m"))
        self.assertIsNone(r.unload("org/m"))
        self.assertEqual(self.sent, [
            {"url": "http://127.0.0.1:8080/models/load", "body": {"model": "org/m"}, "key": KEY, "timeout": 30},
            {"url": "http://127.0.0.1:8080/models/unload", "body": {"model": "org/m"}, "key": KEY, "timeout": 30}])

    def test_load_any_2xx_reply_is_success(self):
        for reply in ({}, [], None, "ok", {"success": False}):
            self.assertIsNone(self.router(request=reply).load("m"))

    def test_load_bad_model(self):
        r = self.router(request={"success": True})
        for bad in ("", None, 5, ["m"], b"m"):
            with self.assertRaises(llm.LLMError, msg=repr(bad)):
                r.load(bad)
            with self.assertRaises(llm.LLMError, msg=repr(bad)):
                r.unload(bad)
        self.assertEqual(self.sent, [])

    def test_load_failure_raises(self):
        with self.assertRaises(llm.LLMError):
            self.router(request=llm.LLMError("router answered 400: nope")).load("m")
        with self.assertRaises(llm.RouterUnavailable):
            self.router(request=llm.RouterUnavailable("down")).unload("m")

    # wait_status()
    def scripted_models(self, *steps):
        """Each step: a status string for model "m", or an exception to raise."""
        steps = list(steps)

        def request(url):
            s = steps.pop(0) if len(steps) > 1 else steps[0]
            if isinstance(s, Exception):
                return s
            return {"data": [{"id": "other", "status": {"value": "loaded"}},
                             {"id": "m", "status": {"value": s}}]}
        return self.router(request=request)

    def fake_time(self):
        self.now = [0.0]
        self.slept = []

        def sleep(s):
            self.slept.append(s)
            self.now[0] += s
        return sleep, (lambda: self.now[0])

    def test_wait_status_immediate(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models("loaded")
        self.assertTrue(r.wait_status("m", "loaded", sleep=sleep, clock=clock))
        self.assertEqual(self.slept, [])
        self.assertEqual(len(self.sent), 1)

    def test_wait_status_after_polls(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models("unloaded", "loading", "loading", "loaded")
        self.assertTrue(r.wait_status("m", "loaded", sleep=sleep, clock=clock))
        self.assertEqual(self.slept, [2, 2, 2])
        self.assertEqual(len(self.sent), 4)

    def test_wait_status_unloaded(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models("loaded", "unloaded")
        self.assertTrue(r.wait_status("m", "unloaded", sleep=sleep, clock=clock))

    def test_wait_status_timeout(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models("loading")
        self.assertFalse(r.wait_status("m", "loaded", timeout=10, sleep=sleep, clock=clock))
        self.assertLessEqual(self.now[0], 12)
        self.assertGreaterEqual(self.now[0], 10)
        self.assertLessEqual(len(self.sent), 7)

    def test_wait_status_missing_model_times_out(self):
        sleep, clock = self.fake_time()
        r = self.router(request={"data": []})
        self.assertFalse(r.wait_status("m", "loaded", timeout=4, sleep=sleep, clock=clock))

    def test_wait_status_fail_returns_false_at_once(self):
        for bad in ("failed", "load_error", "ERROR"):
            sleep, clock = self.fake_time()
            r = self.scripted_models("loading", bad, "loaded")
            self.assertFalse(r.wait_status("m", "loaded", sleep=sleep, clock=clock), msg=bad)
            self.assertEqual(len(self.sent), 2, msg=bad)

    def test_wait_status_tolerates_router_unavailable(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models(llm.RouterUnavailable("503 loading"), llm.RouterUnavailable("refused"),
                                 "loaded")
        self.assertTrue(r.wait_status("m", "loaded", sleep=sleep, clock=clock))
        self.assertEqual(len(self.sent), 3)

    def test_wait_status_router_unavailable_until_timeout(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models(llm.RouterUnavailable("down"))
        self.assertFalse(r.wait_status("m", "loaded", timeout=6, sleep=sleep, clock=clock))

    def test_wait_status_other_llm_error_propagates(self):
        sleep, clock = self.fake_time()
        r = self.scripted_models("loading", llm.LLMError("router answered 401: no"), "loaded")
        with self.assertRaises(llm.LLMError) as cm:
            r.wait_status("m", "loaded", sleep=sleep, clock=clock)
        self.assertNotIsInstance(cm.exception, llm.RouterUnavailable)


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
