import conftest_paths  # noqa: F401
import json, os, shutil, tempfile, unittest

import bench

def row(b, ub, p, n, ts=100.0):
    """One llama-bench JSONL row."""
    return json.dumps({"n_batch": b, "n_ubatch": ub, "n_prompt": p,
                       "n_gen": n, "avg_ts": ts, "stddev_ts": 1.0})


class FakeProc:
    """llama-bench stand-in: fixed stdout, fixed exit code."""
    def __init__(self, lines=(), code=0):
        self.stdout = iter(lines)
        self.code = code
        self.terminated = False

    def wait(self):
        return self.code

    def terminate(self):
        self.terminated = True


class TestGrid(unittest.TestCase):
    def test_ubs_above_b_are_dropped(self):
        self.assertEqual(bench.grid([512], [128, 256, 1024]),
                         [{"b": 512, "ubs": [128, 256]}])

    def test_one_array_of_ubs_per_b(self):
        g = bench.grid([1024, 512], [256, 128])
        self.assertEqual([(e["b"], e["ubs"]) for e in g],
                         [(512, [128, 256]), (1024, [128, 256])])

    def test_b_without_any_fitting_ub_is_dropped(self):
        self.assertEqual(bench.grid([64], [128]), [])


class TestBuildArgv(unittest.TestCase):
    def test_bench_dialect_not_server_flags(self):
        argv = bench.build_argv("llama-bench", "m.gguf", 512, [128, 256])
        self.assertEqual(argv[:7], ["llama-bench", "-m", "m.gguf", "-b", "512",
                                    "-ub", "128,256"])
        self.assertIn("-pg", argv)
        self.assertIn("5000,2500", argv)
        self.assertIn("-o", argv)

    def test_reps_capped_at_two(self):
        argv = bench.build_argv("x", "m", 512, [128], reps=9)
        self.assertEqual(argv[argv.index("-r") + 1], "2")

    def test_none_workloads_use_defaults(self):
        argv = bench.build_argv("x", "m", 512, [128], p=None, n=None, pg=None)
        self.assertEqual(argv[argv.index("-p") + 1], "5000")
        self.assertEqual(argv[argv.index("-n") + 1], "2500")
        self.assertEqual(argv[argv.index("-pg") + 1], "5000,2500")

    def test_ngl_99_and_999_are_refused(self):
        for bad in (99, 999):
            with self.assertRaises(ValueError):
                bench.build_argv("x", "m", 512, [128],
                                 offload={"gpu-layers": bad})

    def test_offload_uses_bench_spellings(self):
        argv = bench.build_argv("x", "m", 512, [128],
                                offload={"gpu-layers": 66,
                                         "device": "Vulkan0,Vulkan1"})
        self.assertEqual(argv[argv.index("-ngl") + 1], "66")
        # llama-bench reads a comma as "run this parameter again", so the
        # model's comma-separated device list must arrive as one value
        self.assertEqual(argv[argv.index("-dev") + 1], "Vulkan0/Vulkan1")

    def test_device_list_is_normalized_from_either_spelling(self):
        self.assertEqual(bench.devlist("Vulkan0,Vulkan1,Vulkan2,Vulkan3"),
                         "Vulkan0/Vulkan1/Vulkan2/Vulkan3")
        self.assertEqual(bench.devlist("Vulkan0/Vulkan1"), "Vulkan0/Vulkan1")
        self.assertEqual(bench.devlist(" Vulkan0 "), "Vulkan0")

    def test_cache_types_are_passed_to_the_bench(self):
        argv = bench.build_argv("x", "m", 512, [128],
                                offload={"cache-type-k": "q5_1",
                                         "cache-type-v": "q5_1"})
        self.assertEqual(argv[argv.index("-ctk") + 1], "q5_1")
        self.assertEqual(argv[argv.index("-ctv") + 1], "q5_1")


class TestWorkload(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(bench.workload(), (5000, 2500, (5000, 2500)))

    def test_empty_pg_skips_the_combined_test(self):
        self.assertEqual(bench.workload(pg="")[2], [])
        self.assertEqual(bench.workload(pg=[])[2], [])

    def test_pg_needs_both_values(self):
        with self.assertRaises(ValueError) as cm:
            bench.workload(pg="5000")
        self.assertIn("pg", str(cm.exception))

    def test_a_typo_names_its_parameter(self):
        with self.assertRaises(ValueError) as cm:
            bench.workload(n="two thousand")
        self.assertIn("n", str(cm.exception))

    def test_pg_values_must_be_numbers(self):
        with self.assertRaises(ValueError) as cm:
            bench.workload(pg="5000,x")
        self.assertIn("pg", str(cm.exception))


class TestRows(unittest.TestCase):
    def test_parse_line_skips_noise(self):
        self.assertIsNone(bench.parse_line("ggml_vulkan: warmup"))
        self.assertIsNone(bench.parse_line("{broken"))
        self.assertIsNone(bench.parse_line('{"n_batch": 1}'))

    def test_kind_from_token_counters(self):
        self.assertEqual(bench.kind({"n_prompt": 5000, "n_gen": 0}), "pp")
        self.assertEqual(bench.kind({"n_prompt": 0, "n_gen": 2500}), "tg")
        self.assertEqual(bench.kind({"n_prompt": 5000, "n_gen": 2500}), "pg")

    def test_result_keeps_the_devices_it_measured_on(self):
        r = bench.result({"avg_ts": 1.0, "n_prompt": 5000, "n_gen": 0,
                          "devices": "Vulkan0/Vulkan1/Vulkan2/Vulkan3"})
        self.assertEqual(r["devices"], "Vulkan0/Vulkan1/Vulkan2/Vulkan3")


class TestBenchManager(unittest.TestCase):
    def test_second_start_is_refused(self):
        m = bench.BenchManager(popen=lambda *a, **k: FakeProc())
        m.state["running"] = True
        with self.assertRaises(RuntimeError):
            m.start({"bench_bin": "x", "model_file": "m", "grid": [],
                     "offload": {}, "reps": 1})

    def test_rows_land_in_per_b_arrays(self):
        m = bench.BenchManager(popen=lambda *a, **k: FakeProc([
            row(512, 128, 5000, 0), row(512, 128, 0, 2500),
            row(512, 256, 5000, 0), row(512, 256, 0, 2500),
        ]))
        m.start({"bench_bin": "x", "model_file": "m", "offload": {},
                 "grid": [{"b": 512, "ubs": [128, 256]}], "pg": False,
                 "reps": 2})
        m._thread.join(5)
        st = m.progress()
        self.assertFalse(st["running"])
        self.assertEqual(st["phase"], "done")
        self.assertEqual(st["total_rows"], 4)
        self.assertEqual(st["done_rows"], 4)
        self.assertEqual([u["rows"]["pp"]["ts"] for u in st["grid"][0]["ubs"]],
                         [100.0, 100.0])

    def test_failed_process_is_reported(self):
        m = bench.BenchManager(popen=lambda *a, **k: FakeProc(["boom"], code=1))
        m.start({"bench_bin": "x", "model_file": "m", "offload": {},
                 "grid": [{"b": 512, "ubs": [128]}], "pg": False, "reps": 1})
        m._thread.join(5)
        self.assertEqual(m.progress()["phase"], "error")

    def test_cancel_terminates_the_process(self):
        m = bench.BenchManager(popen=lambda *a, **k: FakeProc())
        proc = FakeProc()
        m._proc, m.state["running"] = proc, True
        self.assertTrue(m.cancel())
        self.assertTrue(proc.terminated)

    def test_cancel_without_a_run(self):
        self.assertFalse(bench.BenchManager().cancel())


class TestRecommend(unittest.TestCase):
    def test_smaller_ub_wins_within_three_percent(self):
        rows = [{"b": 512, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 98.0}}},
                                   {"ub": 256, "rows": {"pg": {"ts": 100.0}}}]}]
        self.assertEqual(bench.recommend(rows), {"b": 512, "ub": 128, "ts": 98.0})

    def test_faster_cell_wins_outside_the_band(self):
        rows = [{"b": 512, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 90.0}}},
                                   {"ub": 256, "rows": {"pg": {"ts": 100.0}}}]}]
        self.assertEqual(bench.recommend(rows), {"b": 512, "ub": 256, "ts": 100.0})

    def test_smaller_b_breaks_a_tie(self):
        rows = [{"b": 1024, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 100.0}}}]},
                {"b": 512, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 100.0}}}]}]
        self.assertEqual(bench.recommend(rows), {"b": 512, "ub": 128, "ts": 100.0})

    def test_no_pg_row_means_no_recommendation(self):
        rows = [{"b": 512, "ubs": [{"ub": 128, "rows": {"pp": {"ts": 100.0}}}]}]
        self.assertIsNone(bench.recommend(rows))


class TestDeltas(unittest.TestCase):
    def test_percent_change_against_the_previous_grid(self):
        prev = [{"b": 512, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 100.0}}}]}]
        now = [{"b": 512, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 110.0}}}]}]
        self.assertEqual(bench.deltas(now, prev), {"512:128:pg": 10.0})

    def test_no_previous_run_means_no_deltas(self):
        now = [{"b": 512, "ubs": [{"ub": 128, "rows": {"pg": {"ts": 110.0}}}]}]
        self.assertEqual(bench.deltas(now, None), {})


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_finished_run_is_recorded_with_its_raw_stream(self):
        m = bench.BenchManager(
            popen=lambda *a, **k: FakeProc([row(512, 128, 5000, 0),
                                            row(512, 128, 0, 2500)]),
            logdir=self.dir)
        m.start({"bench_bin": "x", "model_file": "m.gguf", "offload": {},
                 "grid": [{"b": 512, "ubs": [128]}], "pg": False, "reps": 1,
                 "model": "m", "engine": "b11552-vulkan"})
        m._thread.join(5)
        recs = bench.read_history(self.dir)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["model"], "m")
        self.assertEqual(recs[0]["engine"], "b11552-vulkan")
        self.assertTrue(os.path.exists(
            os.path.join(self.dir, "bench", recs[0]["raw_file"])))

    def test_a_repeated_cell_keeps_the_first_number(self):
        m = bench.BenchManager(popen=lambda *a, **k: FakeProc([
            row(512, 128, 5000, 0, ts=100.0),
            row(512, 128, 5000, 0, ts=101.0),
        ]))
        m.start({"bench_bin": "x", "model_file": "m", "offload": {},
                 "grid": [{"b": 512, "ubs": [128]}], "pg": False, "reps": 1})
        m._thread.join(5)
        st = m.progress()
        self.assertEqual(st["done_rows"], 1)      # cells, not lines
        self.assertEqual(st["extra_rows"], 1)     # the repeat is kept aside
        self.assertEqual(st["grid"][0]["ubs"][0]["rows"]["pp"]["ts"], 100.0)

    def test_the_last_run_survives_a_restart(self):
        m = bench.BenchManager(
            popen=lambda *a, **k: FakeProc([row(512, 128, 5000, 0)]),
            logdir=self.dir)
        m.start({"bench_bin": "x", "model_file": "m.gguf", "offload": {},
                 "grid": [{"b": 512, "ubs": [128]}], "pg": False, "reps": 1,
                 "model": "m", "engine": "b11552-vulkan"})
        m._thread.join(5)
        again = bench.BenchManager(popen=lambda *a, **k: FakeProc(),
                                   logdir=self.dir)
        st = again.progress()
        self.assertEqual(st["phase"], "done")
        self.assertEqual(st["model"], "m")
        self.assertEqual(st["done_rows"], 1)


if __name__ == "__main__":
    unittest.main()
