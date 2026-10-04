import conftest_paths  # noqa: F401
import io, json, os, re, shutil, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import config
import embers_cli
from embers import jobs, llm, lock
from embers.llm import LLMError, RouterUnavailable
from embers_testkit import NOW, LockHolder

TPL = os.path.join(config.ROOT, "templates", "morning-brief.json")
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028\u2029\u202a-\u202e"
                   "\u2060\u2066-\u2069\ufeff\U000e0000-\U000e007f]")


def scripted(messages, schema):
    """The replies a cooperative model would give, keyed by schema."""
    if "ops" in schema["properties"]:
        sha = re.findall(r"=== raw ([0-9a-f]{12})", messages[-1]["content"])[0]
        return {"ops": [{"op": "add", "page": "projects/acme", "kind": "loop",
                         "text": "Waiting on Sam for the signed SOW",
                         "evidence": [{"raw": sha, "quote": "I'll send the signed SOW by Friday"}]}]}
    if "headline" in schema["properties"]:
        iid = re.findall(r"\[(it-[0-9a-f]{8})\]", messages[-1]["content"])[0]
        return {"headline": "One loose end", "sections": [{"title": "Waiting on others",
                "bullets": [{"item": iid, "text": "Sam owes you the signed SOW."}]}]}
    return {"pairs": []}


class FakeRouter:
    """Stands in for embers.llm.Router: scripted replies keyed by schema."""
    loaded = "qwen3.5-9b"
    ctx = 16384
    fail = None              # an exception every model call raises

    def __init__(self, cfg):
        self.cfg = cfg

    def loaded_model(self):
        if isinstance(self.loaded, Exception):
            raise self.loaded
        return self.loaded

    def n_ctx(self, model):
        return self.ctx

    def llm(self, model, autoload=True):
        assert autoload is False, "the CLI must never let a request load a model"

        def call(messages, schema, max_tokens=2048):
            if self.fail is not None:
                raise self.fail
            return scripted(messages, schema), {"prompt_tokens": 1, "completion_tokens": 1}
        return call


class CliCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.dir, "config.json")
        self.addCleanup(setattr, config, "CONFIG", self._orig)
        # A port nothing listens on: a test that wrongly reaches the real Router fails fast
        # instead of touching the user's router on 8080.
        config.update({"embers_dir": os.path.join(self.dir, "embers"), "router_port": 9})
        self.notes = os.path.join(self.dir, "notes")
        os.makedirs(self.notes)
        with open(os.path.join(self.notes, "acme.md"), "w", encoding="utf-8") as f:
            f.write("Sam: I'll send the signed SOW by Friday.")
        for k in ("loaded", "ctx", "fail"):
            self.addCleanup(setattr, FakeRouter, k, getattr(FakeRouter, k))
        self.err = ""

    def cli(self, *argv, router_cls=FakeRouter):
        out, err = io.StringIO(), io.StringIO()
        try:
            code = embers_cli.main(list(argv), now=NOW, router_cls=router_cls, out=out, err=err)
        finally:
            self.err = err.getvalue()
        return code, out.getvalue()

    def create(self):
        code, out = self.cli("create", "morning", "--template", TPL, "--bind", f"notes={self.notes}")
        self.assertEqual(code, 0, out + self.err)


class CliTest(CliCase):
    def test_create_run_list_lint(self):
        code, out = self.cli("create", "morning", "--template", TPL, "--bind", f"notes={self.notes}")
        self.assertEqual(code, 0)
        self.assertIn("Created morning", out)
        self.assertEqual(self.cli("list")[1].strip(), "morning")
        code, out = self.cli("run", "morning")
        self.assertEqual(code, 0)
        self.assertIn("Using qwen3.5-9b (context 16384)", out)
        self.assertIn("ingest: ok:", out)
        self.assertIn("brief: ok:", out)
        brief = os.path.join(self.dir, "embers", "morning", "briefs", "2026-10-05.md")
        with open(brief, encoding="utf-8") as f:
            self.assertIn("Sam owes you the signed SOW.", f.read())
        self.assertIn("lint: ok:", self.cli("lint", "morning")[1])

    def test_errors(self):
        with self.assertRaises(SystemExit):
            self.cli("brief", "missing")
        with self.assertRaises(SystemExit):
            self.cli("create", "x", "--template", "t.json", "--bind", "novalue")

    def test_no_loaded_model(self):
        self.create()
        FakeRouter.loaded = None
        with self.assertRaises(SystemExit) as cm:
            self.cli("ingest", "morning")
        self.assertIn("No model is loaded", str(cm.exception))


class HardeningTest(CliCase):
    def test_every_job_gets_the_model_context(self):
        self.create()
        calls = {}
        for name in ("ingest", "brief", "lint"):
            calls[name] = mock.patch.object(jobs, name, wraps=getattr(jobs, name)).start()
        self.addCleanup(mock.patch.stopall)
        self.assertEqual(self.cli("run", "morning")[0], 0)
        self.assertEqual(self.cli("lint", "morning")[0], 0)
        for name, m in calls.items():
            self.assertEqual(m.call_args.kwargs.get("n_ctx"), 16384, name)

    def test_garbage_context_falls_back_and_huge_is_clamped(self):
        self.create()
        for bad in (None, "abc", -5, 0, True, 3.5, [16384]):
            FakeRouter.ctx = bad
            with mock.patch.object(jobs, "lint", wraps=jobs.lint) as m:
                code, out = self.cli("lint", "morning")
            self.assertEqual(code, 0, repr(bad))
            self.assertIn(f"(context {llm.DEFAULT_N_CTX})", out, repr(bad))
            self.assertEqual(m.call_args.kwargs["n_ctx"], llm.DEFAULT_N_CTX, repr(bad))
        FakeRouter.ctx = 10 ** 12
        code, out = self.cli("lint", "morning")
        self.assertIn(f"(context {embers_cli.MAX_N_CTX})", out)
        FakeRouter.ctx = "32768"              # numeric text from a lax server is fine
        self.assertIn("(context 32768)", self.cli("lint", "morning")[1])

    def test_small_context_is_reported(self):
        self.create()
        FakeRouter.ctx = 1024
        code, out = self.cli("run", "morning")
        self.assertNotEqual(code, 0)
        self.assertIn("ingest: failed:", out)
        self.assertIn("below the minimum", out)

    def test_locked_ember_is_refused(self):
        self.create()
        root = os.path.join(self.dir, "embers", "morning")
        holder = LockHolder(root)                 # a real second process holds the lock
        self.addCleanup(holder.kill)
        with mock.patch.object(jobs, "ingest", wraps=jobs.ingest) as m:
            code, out = self.cli("run", "morning")
        self.assertEqual(code, 1)
        self.assertIn(f"morning is already running in another process (pid {holder.pid}; lock: "
                      f"{os.path.join(root, lock.LOCK_NAME)})", self.err)
        m.assert_not_called()
        holder.release()
        self.assertEqual(self.cli("run", "morning")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(root, lock.LOCK_NAME)))

    def test_busy_raised_inside_a_job_is_not_reported_as_locked(self):
        self.create()
        with mock.patch.object(jobs, "ingest", side_effect=lock.Busy(4242, "", "elsewhere")):
            code, out = self.cli("ingest", "morning")
        self.assertEqual(code, 1)
        self.assertNotIn("already running", self.err)
        self.assertIn("error: Busy", self.err)

    def test_bad_ember_names_are_refused(self):
        for bad in ("../escape", "..", "Morning", "con", "COM1", "lpt9", "a/b", "a\\b", "", "_x",
                    "x" * 65, "nul.txt"):
            with self.assertRaises(SystemExit) as cm:
                self.cli("create", bad, "--template", TPL, "--bind", f"notes={self.notes}")
            self.assertIn("invalid ember name", str(cm.exception), repr(bad))
            with self.assertRaises(SystemExit) as cm:
                self.cli("brief", bad)
            self.assertIn("invalid ember name", str(cm.exception), repr(bad))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "escape")))

    def test_ember_dir_must_stay_under_embers_dir(self):
        outside = os.path.join(self.dir, "outside")
        os.makedirs(outside)
        base = os.path.join(self.dir, "embers")
        os.makedirs(base)
        try:
            os.symlink(outside, os.path.join(base, "morning"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        with self.assertRaises(SystemExit) as cm:
            self.cli("create", "morning", "--template", TPL, "--bind", f"notes={self.notes}")
        self.assertIn("outside", str(cm.exception))
        self.assertEqual(os.listdir(outside), [])

    def test_router_down_before_any_job(self):
        self.create()
        FakeRouter.loaded = RouterUnavailable("router unreachable or unreadable: ConnectionRefusedError")
        code, out = self.cli("run", "morning")
        self.assertEqual(code, 1)
        self.assertIn("start the router", self.err)
        self.assertNotIn("Traceback", self.err)

    def test_router_down_during_ingest_stops_the_run(self):
        self.create()
        FakeRouter.fail = RouterUnavailable("router answered 503: loading model")
        with mock.patch.object(jobs, "brief") as brief:
            code, out = self.cli("run", "morning")
        self.assertEqual(code, 1)
        self.assertIn("ingest: failed:", out)
        self.assertIn("start the router", self.err)
        brief.assert_not_called()

    def test_router_down_during_brief_is_detected_from_the_run(self):
        self.create()
        self.assertEqual(self.cli("ingest", "morning")[0], 0)
        FakeRouter.fail = RouterUnavailable("router answered 503: loading model")
        code, out = self.cli("brief", "morning")
        self.assertEqual(code, 1)
        self.assertIn("brief: partial:", out)       # the plain-list fallback was still written
        self.assertIn("start the router", self.err)

    def test_other_router_errors_are_clean(self):
        self.create()
        FakeRouter.loaded = LLMError("router answered 401: bad key")
        code, out = self.cli("ingest", "morning")
        self.assertEqual(code, 1)
        self.assertIn("error: router answered 401: bad key", self.err)
        self.assertNotIn("start the router", self.err)

    def test_unexpected_job_error_is_clean_unless_debugging(self):
        self.create()
        with mock.patch.object(jobs, "brief", side_effect=OSError("disk full")):
            code, out = self.cli("brief", "morning")
        self.assertEqual(code, 1)
        self.assertIn("error: OSError: disk full", self.err)
        self.assertNotIn("Traceback", self.err)
        with mock.patch.dict(os.environ, {"EMBERS_DEBUG": "1"}), \
                mock.patch.object(jobs, "brief", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.cli("brief", "morning")

    def test_debug_needs_exactly_one(self):
        self.create()
        for value in ("0", "", "yes", "true", " 1", "11"):
            with mock.patch.dict(os.environ, {"EMBERS_DEBUG": value}), \
                    mock.patch.object(jobs, "brief", side_effect=OSError("disk full")):
                self.assertEqual(self.cli("brief", "morning")[0], 1, repr(value))
            self.assertIn("error: OSError: disk full", self.err)

    def test_exit_code_fails_closed_on_unknown_status(self):
        self.create()
        for status in ("weird", None, "running", "aborted", "failed"):
            fake = {"run": 0, "status": status, "summary": "s", "flags": [], "router_down": False}
            with mock.patch.object(jobs, "lint", return_value=fake):
                self.assertEqual(self.cli("lint", "morning")[0], 1, repr(status))
        for status in ("ok", "partial"):
            fake = {"run": 0, "status": status, "summary": "s", "flags": [], "router_down": False}
            with mock.patch.object(jobs, "lint", return_value=fake):
                self.assertEqual(self.cli("lint", "morning")[0], 0, repr(status))

    def test_bind_values_and_slot_names_reject_control_chars_and_overlong_values(self):
        for bind in (f"notes={self.notes}\x00x", "notes=a\x1b[2Jb", "no\x00tes=x", "notes=a\nb",
                     "notes=a\u202eb", "notes=" + "a" * 4097):
            with self.assertRaises(SystemExit) as cm:
                self.cli("create", "morning", "--template", TPL, "--bind", bind)
            self.assertNotIn("\x00", str(cm.exception))
            self.assertIsNone(_CTRL.search(str(cm.exception)), repr(bind))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "embers", "morning")))
        long_ok = os.path.join(self.dir, "x" * 10)
        self.assertEqual(embers_cli._parse_binds(["notes=" + long_ok]), {"notes": long_ok})
        self.assertEqual(len(embers_cli._parse_binds(["feed=" + "a" * 4096])["feed"]), 4096)
        with self.assertRaises(SystemExit):
            self.cli("brief", "morn\x00ing")

    def test_safe_strips_invisible_and_format_characters(self):
        cases = ["a\u061cb", "a\u200bb", "a\u200cb", "a\u200db", "a\u200eb", "a\u200fb", "a\ufeffb",
                 "a\u2060b", "a\U000e0041\U000e0042\U000e007fb", "a\U000e0000b"]
        for s in cases:
            self.assertEqual(embers_cli.safe(s), "ab", ascii(s))
        self.assertEqual(embers_cli.safe("a\u2028b\u2029c"), "a b c")    # line breaks become spaces
        self.assertEqual(embers_cli.safe("a\x1b]0;t\x1b\\b\x1bPq#0payload\x1b\\c"), "abc")
        self.assertEqual(embers_cli.safe("a\x1b(0x\x1b(Bb\x1b#8\x1bc"), "axb")

    def test_reason_line_keeps_its_indent(self):
        self.create()
        FakeRouter.fail = LLMError("bad reply")
        out = self.cli("ingest", "morning")[1]
        self.assertIn("\n  reason: LLMError: bad reply", out)

    def test_non_ascii_output_survives_a_strict_cp1252_stream(self):
        self.create()
        FakeRouter.loaded = "qwen-中文"
        FakeRouter.fail = LLMError("model said ☃ snowman")
        buf, ebuf = io.BytesIO(), io.BytesIO()
        out = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
        err = io.TextIOWrapper(ebuf, encoding="cp1252", errors="strict")
        code = embers_cli.main(["ingest", "morning"], now=NOW, router_cls=FakeRouter, out=out, err=err)
        out.flush()
        text = buf.getvalue().decode("cp1252")
        self.assertEqual(code, 1)
        self.assertIn("Using qwen-\\u4e2d\\u6587 (context 16384)", text)
        self.assertIn("  reason: LLMError: model said \\u2603 snowman", text)
        FakeRouter.loaded = LLMError("router answered 500: ☃")
        code = embers_cli.main(["ingest", "morning"], now=NOW, router_cls=FakeRouter, out=out, err=err)
        err.flush()
        self.assertEqual(code, 1)
        self.assertIn("error: router answered 500: \\u2603", ebuf.getvalue().decode("cp1252"))

    def test_feed_error_text_cannot_fake_router_down(self):
        class Evil(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(500, "RouterUnavailable: gotcha")
                self.send_header("Content-Length", "0")
                self.end_headers()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Evil)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        self.assertNotIn(port, (8080, 8090))
        code, out = self.cli("create", "morning", "--template", TPL, "--bind", f"notes={self.notes}",
                             "--bind", f"feed=http://127.0.0.1:{port}/feed.xml")
        self.assertEqual(code, 0, out + self.err)
        code, out = self.cli("run", "morning")
        self.assertIn("RouterUnavailable: gotcha", out)        # the feed's text is shown as a reason
        self.assertIn("ingest: partial:", out)
        self.assertIn("brief: ok:", out)                       # ...but does not stop the run
        self.assertNotIn("start the router", self.err)
        self.assertEqual(code, 0)

    def test_bad_template_is_a_clean_error(self):
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        code, _ = self.cli("create", "morning", "--template", bad)
        self.assertEqual(code, 1)
        self.assertIn("error:", self.err)
        code, _ = self.cli("create", "morning", "--template", os.path.join(self.dir, "nope.json"))
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", self.err)

    def test_terminal_escapes_are_stripped(self):
        self.create()
        evil = "evil\x1b]0;pwned\x07\x1b[2J\x9b31m\u202emodel\r\n"
        FakeRouter.loaded = evil
        code, out = self.cli("lint", "morning")
        self.assertEqual(code, 0)
        self.assertIn("Using evil", out)
        self.assertNotIn("pwned", out)
        self.assertIsNone(_CTRL.search(out), repr(out))
        FakeRouter.loaded = LLMError("router answered 500: \x1b[31mred\x1b]8;;http://x\x07link")
        self.cli("lint", "morning")
        self.assertIn("error: router answered 500: redlink", self.err)
        self.assertIsNone(_CTRL.search(self.err.replace("\n", "")), repr(self.err))

    def test_reason_lines_are_sanitised(self):
        self.create()
        FakeRouter.fail = LLMError("model said \x1b[2Jhello\x07")
        code, out = self.cli("ingest", "morning")
        self.assertIn("ingest: failed:", out)
        self.assertIn("model said hello", out)
        self.assertIsNone(_CTRL.search(out.replace("\n", "")), repr(out))

    def test_list_shows_last_run_status_including_failures(self):
        self.create()
        FakeRouter.fail = LLMError("bad reply")
        self.cli("ingest", "morning")
        with jobs.Ember(os.path.join(self.dir, "embers", "morning")) as e, e.store.db:
            e.store.start_run("lint", NOW)          # a process that died mid-lint
            e.store.abort_running("lint", NOW)
        code, out = self.cli("list")
        self.assertEqual(code, 0)
        line = out.strip()
        self.assertTrue(line.startswith("morning"), line)
        self.assertIn("ingest failed", line)
        self.assertIn("lint aborted", line)

    def test_list_shows_skipped(self):
        self.create()
        with jobs.Ember(os.path.join(self.dir, "embers", "morning")) as e, e.store.db:
            e.store.record_skip("brief", NOW, "router never came up")
        code, out = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn(f"brief skipped ({NOW.strftime('%Y-%m-%dT%H:%M')})", out)

    def test_list_skips_odd_folders_and_survives_a_broken_ember(self):
        self.create()
        base = os.path.join(self.dir, "embers")
        os.makedirs(os.path.join(base, "broken"))
        with open(os.path.join(base, "broken", "ember.json"), "w", encoding="utf-8") as f:
            f.write("{oops")
        os.makedirs(os.path.join(base, "Bad Name"))
        with open(os.path.join(base, "Bad Name", "ember.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        code, out = self.cli("list")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual([ln.split()[0] for ln in lines], ["broken", "morning"])
        self.assertIn("unreadable", lines[0])


class _StubRouter(BaseHTTPRequestHandler):
    """A tiny llama.cpp router: /v1/models, /props and /v1/chat/completions."""
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.seen.append((self.path, self.headers.get("Authorization")))
        if self.path == "/v1/models":
            self._send({"data": [{"id": "other", "status": {"value": "unloaded"}},
                                 {"id": "stub-model", "status": {"value": "loaded"}}]})
        else:
            self._send({"default_generation_settings": {"n_ctx": 12288}})

    def do_POST(self):
        self.seen.append((self.path, self.headers.get("Authorization")))
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = scripted(req["messages"], req["response_format"]["json_schema"]["schema"])
        self._send({"choices": [{"message": {"content": json.dumps(reply)}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}})


class StubRouterEndToEndTest(CliCase):
    """The real embers.llm.Router against an HTTP stub on 127.0.0.1:<ephemeral>."""

    def test_run_against_stub_router(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubRouter)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        self.assertNotIn(port, (8080, 8090))
        _StubRouter.seen = []
        config.update({"router_port": port, "router_api_key": "", "router_local_key": "stub-key"})
        self.create()
        code, out = self.cli("run", "morning", router_cls=llm.Router)
        self.assertEqual(code, 0, out + self.err)
        self.assertIn("Using stub-model (context 12288)", out)
        self.assertIn("ingest: ok:", out)
        self.assertIn("brief: ok:", out)
        self.assertIn("lint: ok:", self.cli("lint", "morning", router_cls=llm.Router)[1])
        self.assertTrue(_StubRouter.seen)
        self.assertEqual({auth for _, auth in _StubRouter.seen}, {"Bearer stub-key"})
        props = [p for p, _ in _StubRouter.seen if p.startswith("/props?model=stub-model")]
        self.assertTrue(props)
        self.assertTrue(all("autoload=false" in p for p in props), props)
        chats = [p for p, _ in _StubRouter.seen if p.startswith("/v1/chat/completions")]
        self.assertTrue(chats)
        self.assertTrue(all(p.endswith("?autoload=false") for p in chats), chats)

    def test_model_not_loaded_is_refused_without_loading(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubRouter)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        _StubRouter.seen = []
        config.update({"router_port": srv.server_address[1], "router_api_key": "", "router_local_key": "k"})
        self.create()
        code, out = self.cli("ingest", "morning", "--model", "other", router_cls=llm.Router)
        self.assertEqual(code, 1)
        self.assertIn("other is not loaded in the router", self.err)
        self.assertEqual([p for p, _ in _StubRouter.seen], ["/v1/models"])


if __name__ == "__main__":
    unittest.main()
