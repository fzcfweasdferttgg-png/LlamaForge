"""Process slots: one model per llama-server process, for builds the router
can't drive (ik_llama.cpp has no router mode) or a model pinned to another build."""
import conftest_paths  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

import argspec
import slotproc

# The shapes of the two --help formats, trimmed to the knobs under test.
MAIN_HELP = """\
----- common params -----

-t,    --threads N                      number of CPU threads to use during generation
-c,    --ctx-size N                     size of the prompt context (default: 0)
-ngl,  --gpu-layers, --n-gpu-layers N   max. number of layers to store in VRAM
-sm,   --split-mode {none,layer,row}    how to split the model across multiple GPUs
-mg,   --main-gpu INDEX                 the GPU to use for the model
-dev,  --device <dev1,dev2,..>          comma-separated list of devices to use for offloading
--mmap, --no-mmap                       whether to memory-map model (default: enabled)
--jinja, --no-jinja                     whether to use jinja template engine for chat
-fa,   --flash-attn [on|off|auto]       set Flash Attention use ('on', 'off', or 'auto')
--mmproj FILE                           path to a multimodal projector file
--no-mmproj                             explicitly disable multimodal projector
--mmproj-auto, --no-mmproj-auto         whether to use a projector found beside -hf (default: enabled)
--log-colors [on|off|auto]              colored logging (default: 'auto')
-rea,  --reasoning [on|off|auto]        use reasoning/thinking in the chat
--embedding, --embeddings               restrict to embedding use (default: disabled)
--reasoning-budget N                    controls the amount of thinking allowed
--spec-type [none|draft-mtp]            type of speculative decoding
--fit [on|off]                          whether to adjust unset arguments to fit in device memory
--alias STRING                          set alias for model name
--api-key KEY                           API key to use for authentication
--host HOST                             ip address to listen
--port PORT                             port to listen
--metrics                               enable prometheus compatible metrics endpoint
-m,    --model FNAME                    model path
"""

IK_HELP = """\
general:

  -t,    --threads N              number of threads to use during generation (default: 8)
  -c,    --ctx-size N             size of the prompt context (default: 0, 0 = loaded from model)
  -no-fa, --no-flash-attn         disable Flash Attention (default: enabled)
  -fa, --flash-attn (auto|on|off|0|1)
                                  set Flash Attention
         --no-mmap                do not memory-map model
         --jinja                  use jinja template for chat
  -ngl,  --gpu-layers N           number of layers to store in VRAM
  -sm,   --split-mode SPLIT_MODE  how to split the model across multiple GPUs
  -dev,   --device dev1,dev2      comma-separated list of devices to use for offloading
  -mg,   --main-gpu i             the GPU to use for the model
         --mmproj FILE            path to a multimodal projector file
         --reasoning-budget N     thinking budget
  -rea,  --reasoning              [on|off|auto]Use reasoning/thinking in the chat
         --embedding(s)           restrict to embedding use (default: disabled)
         --api-key KEY            API key to use for authorization
         --host HOST              ip address to listen (default: 127.0.0.1)
         --port PORT              port to listen (default: 8080)
         --metrics                enable prometheus compatible metrics endpoint
  -m,    --model FNAME            model path
"""

MAIN = argspec.parse_help(MAIN_HELP)
IK = argspec.parse_help(IK_HELP)

# the speculative stages, as each build spells them
MAIN_SPEC = argspec.parse_help(
    "--spec-type none,draft-simple,draft-mtp,draft-dflash,ngram-mod\n"
    "                                        comma-separated list of types of speculative decoding\n")
IK_SPEC = argspec.parse_help(IK_HELP + """\
  --spec-type SPEC[:k=v,...]      canonical speculative stage entry; repeat for a supported two-stage chain.
                                  types: none, draft, dflash, mtp, ngram-cache, ngram-simple, ngram-map-k, ngram-map-k4v, ngram-mod, suffix
                                  examples: --spec-type mtp:n_max=1,p_min=0.0
""")


def tr(settings, dst=IK, src=MAIN):
    return slotproc.translate(settings, dst, src)


class Translate(unittest.TestCase):
    def test_values_carry_over_by_name(self):
        argv, dropped = tr({"ctx-size": "32768", "threads": "16"})
        self.assertEqual(argv, ["--ctx-size", "32768", "--threads", "16"])
        self.assertEqual(dropped, [])

    def test_a_knob_known_by_another_spelling(self):
        # models.ini says n-gpu-layers (mainline's last spelling); ik only has -ngl/--gpu-layers
        self.assertEqual(tr({"n-gpu-layers": "99"})[0], ["--gpu-layers", "99"])
        self.assertEqual(tr({"ngl": "99"})[0], ["--gpu-layers", "99"])

    def test_placement_keys(self):
        argv, _ = tr({"device": "CUDA1", "split-mode": "none", "main-gpu": "0"})
        self.assertEqual(argv, ["--device", "CUDA1", "--split-mode", "none", "--main-gpu", "0"])

    def test_false_uses_the_negative_flag(self):
        # llama.cpp's own preset rule: a falsy flag becomes its no- form, if there is one
        self.assertEqual(tr({"mmap": "false"})[0], ["--no-mmap"])

    def test_true_where_only_the_negative_exists_is_the_default(self):
        self.assertEqual(tr({"mmap": "true"}), ([], []))

    def test_true_flag(self):
        self.assertEqual(tr({"jinja": "true"})[0], ["--jinja"])

    def test_false_with_no_negative_is_skipped(self):
        self.assertEqual(tr({"jinja": "off"}), ([], []))

    def test_negative_key(self):
        self.assertEqual(tr({"no-mmap": "true"})[0], ["--no-mmap"])
        # no-mmproj = false: "don't disable it", which is the default; never a bare --mmproj
        self.assertEqual(tr({"no-mmproj": "false"}), ([], []))
        self.assertEqual(tr({"no-mmproj": "true"}, dst=MAIN)[0], ["--no-mmproj"])

    def test_valued_options_pass_values_through(self):
        self.assertEqual(tr({"flash-attn": "on"})[0], ["--flash-attn", "on"])
        self.assertEqual(tr({"mmproj": "D:/m/proj.gguf"})[0], ["--mmproj", "D:/m/proj.gguf"])

    def test_what_the_target_lacks_is_dropped_and_named(self):
        argv, dropped = tr({"spec-type": "draft-mtp", "fit": "on", "ctx-size": "8192"})
        self.assertEqual(argv, ["--ctx-size", "8192"])
        self.assertEqual(dropped, ["spec-type", "fit"])

    def test_a_value_spelled_the_other_builds_way(self):
        """The live ik launch: mainline's draft-mtp went through verbatim and ik
        quit with 'unknown speculative stage type: draft-mtp'."""
        self.assertEqual(tr({"spec-type": "draft-mtp"}, dst=IK_SPEC, src=MAIN_SPEC),
                         (["--spec-type", "mtp"], []))
        self.assertEqual(tr({"spec-type": "mtp"}, dst=MAIN_SPEC, src=IK_SPEC),
                         (["--spec-type", "draft-mtp"], []))

    def test_a_value_the_build_doesnt_list_is_dropped(self):
        # ik has no eagle3 stage; starting with it ends the process at once
        self.assertEqual(tr({"spec-type": "draft-eagle3", "ctx-size": "8192"}, dst=IK_SPEC, src=MAIN_SPEC),
                         (["--ctx-size", "8192"], ["spec-type"]))
        self.assertEqual(tr({"flash-attn": "sometimes"}), ([], ["flash-attn"]))
        self.assertEqual(tr({"flash-attn": "auto"})[0], ["--flash-attn", "auto"])

    def test_a_value_with_a_payload_is_the_builds_to_judge(self):
        self.assertEqual(tr({"spec-type": "mtp:n_max=1,p_min=0.0"}, dst=IK_SPEC, src=MAIN_SPEC)[0],
                         ["--spec-type", "mtp:n_max=1,p_min=0.0"])

    def test_a_list_only_where_the_build_takes_one(self):
        # mainline takes comma-separated stages; ik repeats --spec-type instead
        self.assertEqual(tr({"spec-type": "mtp,ngram-mod"}, dst=MAIN_SPEC, src=IK_SPEC)[0],
                         ["--spec-type", "draft-mtp,ngram-mod"])
        self.assertEqual(tr({"spec-type": "draft-mtp,ngram-mod"}, dst=IK_SPEC, src=MAIN_SPEC),
                         ([], ["spec-type"]))

    def test_unlisted_values_pass_through(self):
        # ik's SPLIT_MODE and dev1,dev2 list nothing to check against
        self.assertEqual(tr({"split-mode": "graph", "device": "CUDA0,CUDA1"})[0],
                         ["--split-mode", "graph", "--device", "CUDA0,CUDA1"])

    def test_a_value_that_restates_the_router_default_is_not_dropped(self):
        """ik lacks --mmproj-auto and --log-colors. A section that only says what
        the router build does by default asks for nothing ik could miss."""
        self.assertEqual(tr({"mmproj-auto": "true"}), ([], []))
        self.assertEqual(tr({"log-colors": "auto"}), ([], []))
        self.assertEqual(tr({"mmproj-auto": "false"}), ([], ["mmproj-auto"]))
        self.assertEqual(tr({"log-colors": "on"}), ([], ["log-colors"]))

    def test_ik_spellings_the_parser_had_to_learn(self):
        # `[on|off|auto]Use reasoning` (no space) and `--embedding(s)`
        self.assertEqual(tr({"reasoning": "off"})[0], ["--reasoning", "off"])
        self.assertEqual(tr({"embeddings": "true"})[0], ["--embedding"])

    def test_keys_the_manager_owns_or_presets_keep(self):
        argv, dropped = tr({"model": "D:/m/a.gguf", "host": "0.0.0.0", "port": "1",
                            "api-key": "k", "alias": "x", "load-on-startup": "true",
                            "stop-timeout": "5", "metrics": "true"})
        self.assertEqual((argv, dropped), ([], []))

    def test_blank_values_are_unset(self):
        self.assertEqual(tr({"ctx-size": "", "threads": None}), ([], []))

    def test_without_the_router_binary_names_match_directly(self):
        self.assertEqual(slotproc.translate({"gpu-layers": "99"}, IK, [])[0],
                         ["--gpu-layers", "99"])
        self.assertEqual(slotproc.translate({"mmap": "false"}, IK, [])[0], ["--no-mmap"])


class ExitReason(unittest.TestCase):
    """A process that crashes says nothing in its log; its exit code is all there is."""

    def test_windows_crash_codes_are_named(self):
        self.assertEqual(slotproc.exit_reason(3221225620),
                         "exit code 0xC0000094: it crashed (integer divide by zero)")
        self.assertIn("DLL", slotproc.exit_reason(0xC0000135))
        self.assertIn("CPU", slotproc.exit_reason(0xC000001D))

    def test_posix_signals(self):
        self.assertEqual(slotproc.exit_reason(-11), "signal 11: it crashed (segmentation fault)")
        self.assertEqual(slotproc.exit_reason(-99), "signal 99")

    def test_plain_codes(self):
        self.assertEqual(slotproc.exit_reason(1), "exit code 1")
        self.assertEqual(slotproc.exit_reason(None), "exit code ?")


class Argv(unittest.TestCase):
    def test_launch_line(self):
        argv, dropped = slotproc.argv_for(
            "D:/ik/llama-server.exe", "qwen",
            {"model": "D:/m/q.gguf", "ctx-size": "8192", "spec-type": "draft-mtp"},
            IK, MAIN, api_key="sekrit")
        self.assertEqual(argv[:3], ["D:/ik/llama-server.exe", "-m", "D:/m/q.gguf"])
        self.assertIn("--metrics", argv)
        self.assertEqual(argv[argv.index("--api-key") + 1], "sekrit")
        self.assertEqual(argv[argv.index("--ctx-size") + 1], "8192")
        self.assertNotIn("--alias", argv)                 # ik has no --alias
        self.assertEqual(dropped, ["spec-type"])

    def test_alias_when_the_build_has_it(self):
        argv, _ = slotproc.argv_for("bin", "qwen", {"model": "q.gguf"}, MAIN, MAIN)
        self.assertEqual(argv[argv.index("--alias") + 1], "qwen")
        self.assertNotIn("--api-key", argv)

    def test_no_model_path(self):
        with self.assertRaises(ValueError):
            slotproc.argv_for("bin", "qwen", {"ctx-size": "1"}, IK, MAIN)


class FakeProc:
    _next = 4000

    def __init__(self, argv):
        FakeProc._next += 1
        self.pid = FakeProc._next
        self.argv = argv
        self.rc = None
        self.terminated = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True
        self.rc = 1

    def kill(self):
        self.rc = -9

    def wait(self, timeout=None):
        return self.rc


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.procs = []
        self.health = {}            # port -> HTTP status; missing = connection refused
        self.busy = set()           # ports someone else holds
        self.table = {}
        self.killed = []
        self.m = self.make()

    def make(self):
        def popen(argv, log):
            p = FakeProc(argv)
            self.procs.append(p)
            return p
        m = slotproc.Manager(self.dir, popen=popen,
                             health=lambda port: self.health.get(port),
                             port_free=lambda port: port not in self.busy,
                             table=lambda: dict(self.table),
                             kill=self.killed.append, sleep=lambda s: None)
        return m

    def start(self, mid="ik-model", base=8100):
        return self.m.start(mid, ["D:/ik/llama-server.exe", "-m", "q.gguf"], base)

    def test_start_picks_a_free_port_and_adds_host_and_port(self):
        self.busy.add(8100)
        ok, err, port = self.start()
        self.assertEqual((ok, err, port), (True, "", 8101))
        argv = self.procs[0].argv
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--port") + 1], "8101")

    def test_two_slots_get_two_ports(self):
        self.start("a")
        self.start("b")
        ports = {mid: s["port"] for mid, s in self.m.status().items()}
        self.assertEqual(ports, {"a": 8100, "b": 8101})

    def test_states_follow_health_and_exit(self):
        _, _, port = self.start()
        self.assertEqual(self.m.status()["ik-model"]["state"], "loading")   # not listening yet
        self.health[port] = 503
        self.assertEqual(self.m.status()["ik-model"]["state"], "loading")
        self.health[port] = 200
        s = self.m.status()["ik-model"]
        self.assertEqual((s["state"], s["endpoint"]), ("ready", f"http://127.0.0.1:{port}"))
        self.procs[0].rc = 1
        s = self.m.status()["ik-model"]
        self.assertEqual((s["state"], s["exit_code"]), ("failed", 1))

    def test_starting_a_running_slot_is_a_no_op(self):
        self.start()
        ok, err, port = self.start()
        self.assertTrue(ok)
        self.assertEqual(len(self.procs), 1)

    def test_a_failed_slot_can_start_again(self):
        self.start()
        self.procs[0].rc = 1
        self.assertEqual(self.m.status()["ik-model"]["state"], "failed")
        ok, _, _ = self.start()
        self.assertTrue(ok)
        self.assertEqual(len(self.procs), 2)

    def test_stop(self):
        _, _, port = self.start()
        self.health[port] = 200
        self.assertEqual(self.m.stop("ik-model"), (True, ""))
        self.assertTrue(self.procs[0].terminated)
        self.assertEqual(self.m.status(), {})

    def test_stop_unknown(self):
        self.assertEqual(self.m.stop("nope"), (True, ""))

    def test_record_on_disk(self):
        _, _, port = self.start()
        with open(os.path.join(self.dir, "slotprocs.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["ik-model"]["pid"], self.procs[0].pid)
        self.assertEqual(rec["ik-model"]["port"], port)
        self.m.stop("ik-model")
        with open(os.path.join(self.dir, "slotprocs.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {})

    def test_a_restarted_panel_adopts_live_slots_and_drops_dead_ones(self):
        self.start("alive")
        self.start("dead")
        alive, dead = self.procs
        self.table = {alive.pid: (1, "D:/ik/llama-server.exe"),
                      dead.pid: (1, "C:/Windows/notepad.exe")}     # pid reused by another program
        self.health[8100] = 200
        m2 = self.make()
        m2.reconcile()
        self.assertEqual(list(m2.status()), ["alive"])
        self.assertEqual(m2.status()["alive"]["state"], "ready")
        # adopted: stopped by verified PID, not a Popen handle
        self.assertEqual(m2.stop("alive"), (True, ""))
        self.assertEqual(self.killed, [alive.pid])

    def test_an_adopted_slot_that_stops_answering_is_gone(self):
        self.start("alive")
        self.table = {self.procs[0].pid: (1, "llama-server")}
        self.health[8100] = 200
        m2 = self.make()
        m2.reconcile()
        del self.health[8100]
        self.assertEqual(m2.status(), {})

    def test_adopted_pid_reused_before_stop_is_not_killed(self):
        self.start("alive")
        pid = self.procs[0].pid
        self.table = {pid: (1, "llama-server")}
        self.health[8100] = 200
        m2 = self.make()
        m2.reconcile()
        self.table = {pid: (1, "explorer.exe")}
        m2.stop("alive")
        self.assertEqual(self.killed, [])

    def test_log_tail(self):
        self.start()
        with open(self.m.log_path("ik-model"), "a", encoding="utf-8") as f:
            f.write("line one\nE gguf_init_from_reader: tensor 'x' has invalid ggml type 142\n")
        self.assertIn("invalid ggml type 142", self.m.log_tail("ik-model"))

    def test_log_names_are_safe(self):
        self.assertNotIn("/", os.path.basename(self.m.log_path("org/model:Q4_K_M")))
        self.assertNotIn(":", os.path.basename(self.m.log_path("org/model:Q4_K_M")))

    def test_no_free_port(self):
        self.busy.update(range(8100, 8100 + slotproc.PORT_SPAN))
        ok, err, port = self.start()
        self.assertFalse(ok)
        self.assertIn("8100", err)


if __name__ == "__main__":
    unittest.main()
