"""pi runs one task with a loaded model: hermetic config, the task on stdin,
the outcome read from its JSONL events (it exits 0 even when every request failed)."""
import conftest_paths  # noqa: F401
import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import unittest

import pirun


def ev(**kw):
    return json.dumps(kw)


def assistant(text="", stop="stop", error=None, usage=(10, 5), tool=None):
    content = [{"type": "text", "text": text}] if text else []
    if tool:
        content.append({"type": "toolCall", "id": "c1", "name": tool, "arguments": {}})
    msg = {"role": "assistant", "content": content, "provider": "llamaforge",
           "usage": {"input": usage[0], "output": usage[1]}, "stopReason": stop}
    if error:
        msg["errorMessage"] = error
    return ev(type="message_end", message=msg)


class ModelsJson(unittest.TestCase):
    def test_one_provider_one_model_key_from_env(self):
        out = pirun.models_json("qwen-9b", "http://127.0.0.1:8080", 32768)
        prov = out["providers"]["llamaforge"]
        self.assertEqual(prov["baseUrl"], "http://127.0.0.1:8080/v1")
        self.assertEqual(prov["api"], "openai-completions")
        # the key never lands in a file: pi interpolates it from the child's env
        self.assertEqual(prov["apiKey"], "$LLAMAFORGE_API_KEY")
        self.assertEqual(prov["models"], [{"id": "qwen-9b", "contextWindow": 32768,
                                           "maxTokens": 8192}])

    def test_endpoint_already_ending_in_v1(self):
        out = pirun.models_json("m", "http://127.0.0.1:8101/v1/", None)
        self.assertEqual(out["providers"]["llamaforge"]["baseUrl"], "http://127.0.0.1:8101/v1")

    def test_unknown_ctx_leaves_pi_its_defaults(self):
        for ctx in (None, "?", "", 0, "abc"):
            m = pirun.models_json("m", "http://x:1", ctx)["providers"]["llamaforge"]["models"][0]
            self.assertEqual(m, {"id": "m"}, ctx)

    def test_ctx_as_string(self):
        m = pirun.models_json("m", "http://x:1", "8192")["providers"]["llamaforge"]["models"][0]
        self.assertEqual(m, {"id": "m", "contextWindow": 8192, "maxTokens": 2048})


class Argv(unittest.TestCase):
    def test_hermetic_flags_and_read_only_default(self):
        a = pirun.argv(["node", "cli.js"], "qwen-9b")
        self.assertEqual(a[:2], ["node", "cli.js"])
        for flag in ("--no-session", "--offline", "--no-extensions", "--no-skills",
                     "--no-prompt-templates", "--no-themes", "--no-approve"):
            self.assertIn(flag, a)
        self.assertEqual(a[a.index("--mode") + 1], "json")
        self.assertEqual(a[a.index("--provider") + 1], "llamaforge")
        self.assertEqual(a[a.index("--model") + 1], "qwen-9b")
        self.assertEqual(a[a.index("--tools") + 1], "read,grep,find,ls")
        self.assertNotIn("--print", a)   # json mode is one-shot too

    def test_tool_sets(self):
        self.assertEqual(pirun.argv(["pi"], "m", "edit")[-1], "read,grep,find,ls,edit,write")
        self.assertEqual(pirun.argv(["pi"], "m", "full")[-1], "read,grep,find,ls,edit,write,bash")

    def test_unknown_tool_set_is_refused(self):
        with self.assertRaises(ValueError):
            pirun.argv(["pi"], "m", "everything")

    def test_task_is_not_on_the_command_line(self):
        self.assertFalse(any("task" in x for x in pirun.argv(["pi"], "m")))


class Locate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.managed = os.path.join(self.d, "agents", "pi")     # never the real install dir
        self.addCleanup(setattr, pirun, "MANAGED_DIR", pirun.MANAGED_DIR)
        pirun.MANAGED_DIR = self.managed

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _pkg(self, base, bin_value):
        pkg = os.path.join(base, "node_modules", "@earendil-works", "pi-coding-agent")
        os.makedirs(os.path.join(pkg, "dist"), exist_ok=True)
        with open(os.path.join(pkg, "package.json"), "w") as f:
            json.dump({"name": "@earendil-works/pi-coding-agent", "bin": bin_value}, f)
        js = os.path.join(pkg, "dist", "cli.js")
        open(js, "w").close()
        return pkg, js

    def test_windows_shim_runs_node_on_the_package_script(self):
        # cmd.exe would re-parse arguments: the .cmd shim is never executed
        pkg, js = self._pkg(self.d, {"pi": "dist/cli.js"})
        shim = os.path.join(self.d, "pi.cmd")
        open(shim, "w").close()
        which = {"pi": shim, "node": "C:/node/node.exe"}.get
        self.assertEqual(pirun.locate("", which=which, is_win=True),
                         ["C:/node/node.exe", os.path.normpath(js)])

    def test_windows_node_beside_the_shim(self):
        pkg, js = self._pkg(self.d, "dist/cli.js")    # bin as a plain string
        shim = os.path.join(self.d, "pi.cmd")
        open(shim, "w").close()
        node = os.path.join(self.d, "node.exe")
        open(node, "w").close()
        which = {"pi": shim}.get
        self.assertEqual(pirun.locate("", which=which, is_win=True),
                         [node, os.path.normpath(js)])

    def test_posix_pi_on_path_runs_directly(self):
        which = {"pi": "/usr/local/bin/pi"}.get
        self.assertEqual(pirun.locate("", which=which, is_win=False), ["/usr/local/bin/pi"])

    def test_configured_js_entry(self):
        pkg, js = self._pkg(self.d, {"pi": "dist/cli.js"})
        which = {"node": "/usr/bin/node"}.get
        self.assertEqual(pirun.locate(js, which=which, is_win=False), ["/usr/bin/node", js])

    def test_configured_package_dir(self):
        pkg, js = self._pkg(self.d, {"pi": "dist/cli.js"})
        which = {"node": "/usr/bin/node"}.get
        self.assertEqual(pirun.locate(pkg, which=which, is_win=True),
                         ["/usr/bin/node", os.path.normpath(js)])

    def test_configured_cmd_shim_on_windows_is_resolved_too(self):
        pkg, js = self._pkg(self.d, {"pi": "dist/cli.js"})
        shim = os.path.join(self.d, "pi.cmd")
        open(shim, "w").close()
        which = {"node": "C:/node/node.exe"}.get
        self.assertEqual(pirun.locate(shim, which=which, is_win=True),
                         ["C:/node/node.exe", os.path.normpath(js)])

    def test_nothing_found(self):
        self.assertIsNone(pirun.locate("", which=lambda n: None, is_win=False))
        self.assertIsNone(pirun.locate(os.path.join(self.d, "missing.js"),
                                       which=lambda n: None, is_win=False))

    def test_managed_copy_beats_pi_on_path(self):
        pkg, js = self._pkg(self.managed, {"pi": "dist/cli.js"})
        which = {"pi": "/usr/local/bin/pi", "node": "/usr/bin/node"}.get
        self.assertEqual(pirun.locate_how("", which=which, is_win=False),
                         (["/usr/bin/node", os.path.normpath(js)], "managed"))
        self.assertEqual(pirun.locate("", which=which, is_win=False), ["/usr/bin/node", os.path.normpath(js)])

    def test_configured_pi_bin_beats_the_managed_copy(self):
        self._pkg(self.managed, {"pi": "dist/cli.js"})
        mine = os.path.join(self.d, "my-pi")
        open(mine, "w").close()
        which = {"node": "/usr/bin/node"}.get
        self.assertEqual(pirun.locate_how(mine, which=which, is_win=False), ([mine], "pi_bin"))

    def test_managed_copy_without_node_falls_back_to_path(self):
        self._pkg(self.managed, {"pi": "dist/cli.js"})
        which = {"pi": "/usr/local/bin/pi"}.get
        self.assertEqual(pirun.locate_how("", which=which, is_win=False), (["/usr/local/bin/pi"], "path"))
        self.assertEqual(pirun.locate_how("", which=lambda n: None, is_win=False), (None, ""))

    def test_js_without_node(self):
        pkg, js = self._pkg(self.d, {"pi": "dist/cli.js"})
        self.assertIsNone(pirun.locate(js, which=lambda n: None, is_win=False))


class ParseEvents(unittest.TestCase):
    def test_answer_tools_turns_usage(self):
        lines = [ev(type="session", id="s"), assistant(tool="ls", stop="toolUse", usage=(7, 3)),
                 ev(type="tool_execution_end", toolCallId="c1", toolName="ls", isError=False),
                 ev(type="turn_end"),
                 assistant("All done.", usage=(10, 5)), ev(type="turn_end"),
                 ev(type="agent_settled")]
        out = pirun.parse_events(lines)
        self.assertEqual(out["text"], "All done.")
        self.assertEqual(out["error"], "")
        self.assertEqual(out["stop"], "stop")
        self.assertEqual(out["tool_calls"], [{"tool": "ls", "error": False}])
        self.assertEqual(out["turns"], 2)
        self.assertEqual(out["usage"], {"input": 17, "output": 8})

    def test_every_request_failed(self):
        lines = [assistant(stop="error", error="Connection error.", usage=(0, 0)),
                 ev(type="auto_retry_start", attempt=1),
                 assistant(stop="error", error="Connection error.", usage=(0, 0)),
                 ev(type="auto_retry_end", success=False, finalError="Connection error.")]
        out = pirun.parse_events(lines)
        self.assertEqual(out["error"], "Connection error.")
        self.assertEqual(out["text"], "")

    def test_a_retry_that_succeeds_clears_the_error(self):
        lines = [assistant(stop="error", error="Connection error."),
                 ev(type="auto_retry_start", attempt=1), assistant("fine"),
                 ev(type="auto_retry_end", success=True)]
        out = pirun.parse_events(lines)
        self.assertEqual((out["text"], out["error"]), ("fine", ""))

    def test_noise_is_ignored(self):
        out = pirun.parse_events(["", "not json", "[1,2]", ev(type="message_end"),
                                  ev(type="message_end", message={"role": "user"}),
                                  assistant("ok")])
        self.assertEqual(out["text"], "ok")

    def test_text_of_the_last_answer_wins(self):
        out = pirun.parse_events([assistant("first", stop="toolUse", tool="read"),
                                  assistant("second")])
        self.assertEqual(out["text"], "second")


FAKE_PI = textwrap.dedent(r'''
    import json, os, sys, time
    task = sys.stdin.read()
    mode = os.environ.get("FAKE_MODE", "ok")
    def out(**kw):
        print(json.dumps(kw), flush=True)
    if mode == "sleep":
        time.sleep(60)
    if mode == "crash":
        sys.stderr.write("node: boom\n")
        sys.exit(3)
    if mode == "fail":
        out(type="message_end", message={"role": "assistant", "content": [],
            "stopReason": "error", "errorMessage": "Connection error.", "usage": {}})
        sys.exit(0)
    if mode in ("flail", "quiet"):
        for ok in ([False, False] if mode == "flail" else [True]):
            out(type="tool_execution_start", toolName="read")
            out(type="tool_execution_end", toolName="read", isError=not ok)
        out(type="message_end", message={"role": "assistant", "stopReason": "stop",
            "content": [], "usage": {}})
        sys.exit(0)
    cfg = json.load(open(os.path.join(os.environ["PI_CODING_AGENT_DIR"], "models.json")))
    prov = cfg["providers"]["llamaforge"]
    out(type="tool_execution_start", toolName="read")
    out(type="tool_execution_end", toolName="read", isError=False)
    out(type="turn_end")
    report = {"task": task, "key": os.environ.get("LLAMAFORGE_API_KEY"),
              "base": prov["baseUrl"], "model": prov["models"][0]["id"],
              "cwd": os.getcwd(), "argv": sys.argv[1:], "home": os.environ["PI_CODING_AGENT_DIR"]}
    out(type="message_end", message={"role": "assistant", "stopReason": "stop",
        "content": [{"type": "text", "text": json.dumps(report)}],
        "usage": {"input": 3, "output": 4}})
''')


class Run(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.fake = os.path.join(self.d, "fakepi.py")
        with open(self.fake, "w") as f:
            f.write(FAKE_PI)
        self.work = os.path.join(self.d, "work")
        os.makedirs(self.work)
        self.cmd = [sys.executable, self.fake]
        os.environ.pop("FAKE_MODE", None)

    def tearDown(self):
        os.environ.pop("FAKE_MODE", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def run_pi(self, **kw):
        args = dict(task='fix it & echo "x" | %PATH%', model="qwen-9b",
                    endpoint="http://127.0.0.1:8080", key="sekret", cwd=self.work,
                    cmd=self.cmd)
        args.update(kw)
        return pirun.run(**args)

    def test_a_task_end_to_end(self):
        seen = []
        out = self.run_pi(progress=seen.append)
        self.assertTrue(out["ok"], out)
        rep = json.loads(out["text"])
        self.assertEqual(rep["task"], 'fix it & echo "x" | %PATH%')   # verbatim, via stdin
        self.assertEqual(rep["key"], "sekret")
        self.assertEqual(rep["base"], "http://127.0.0.1:8080/v1")
        self.assertEqual(rep["model"], "qwen-9b")
        self.assertEqual(os.path.normcase(os.path.realpath(rep["cwd"])),
                         os.path.normcase(os.path.realpath(self.work)))
        self.assertNotIn("sekret", " ".join(rep["argv"]))
        self.assertFalse(os.path.exists(rep["home"]))   # the throwaway config is gone
        self.assertEqual(out["tool_calls"], [{"tool": "read", "error": False}])
        self.assertEqual(out["usage"], {"input": 3, "output": 4})
        self.assertTrue(any("read" in s for s in seen), seen)

    def test_failure_with_exit_code_zero(self):
        os.environ["FAKE_MODE"] = "fail"
        out = self.run_pi()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "Connection error.")

    def test_no_answer_and_every_tool_failed_is_a_failure(self):
        """A model too weak for tool calling (seen live: a 2-bit 4B) ends with
        stop, no text and only failed tools; that is not a success."""
        os.environ["FAKE_MODE"] = "flail"
        out = self.run_pi()
        self.assertFalse(out["ok"], out)
        self.assertIn("every tool call failed", out["error"])

    def test_no_answer_after_a_working_tool_is_still_ok(self):
        os.environ["FAKE_MODE"] = "quiet"
        out = self.run_pi()
        self.assertTrue(out["ok"], out)

    def test_crash_reports_stderr(self):
        os.environ["FAKE_MODE"] = "crash"
        out = self.run_pi()
        self.assertFalse(out["ok"])
        self.assertIn("exited with code 3", out["error"])
        self.assertIn("boom", out["error"])

    def test_timeout_kills_it(self):
        os.environ["FAKE_MODE"] = "sleep"
        t = time.monotonic()
        out = self.run_pi(timeout=1)
        self.assertLess(time.monotonic() - t, 20)
        self.assertFalse(out["ok"])
        self.assertTrue(out["timed_out"])
        self.assertIn("timeout", out["error"])

    def test_cancel_kills_it(self):
        os.environ["FAKE_MODE"] = "sleep"
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        out = self.run_pi(cancel=cancel, timeout=60)
        self.assertFalse(out["ok"])
        self.assertTrue(out["cancelled"])

    def test_long_answers_are_capped(self):
        old = pirun.MAX_TEXT
        pirun.MAX_TEXT = 20
        try:
            out = self.run_pi()
        finally:
            pirun.MAX_TEXT = old
        self.assertTrue(out["text"].endswith("[truncated]"), out["text"])

    def test_missing_cwd(self):
        out = self.run_pi(cwd=os.path.join(self.d, "nope"))
        self.assertFalse(out["ok"])
        self.assertIn("not a directory", out["error"])


if __name__ == "__main__":
    unittest.main()
