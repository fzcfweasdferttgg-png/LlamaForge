import conftest_paths  # noqa: F401
import unittest
import autotune


class FakeClock:
    def __init__(self, step=1.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class TestRefine(unittest.TestCase):
    def test_keeps_fastest_candidate(self):
        base = {"n-gpu-layers": "99", "ubatch-size": "512"}
        seen = []

        def load_fn(knobs):
            seen.append(knobs.get("ubatch-size"))

        # 512 -> 10 tok/s, 1024 -> 30 tok/s
        speeds = {"512": 10.0, "1024": 30.0}
        holder = {"cur": "512"}

        def load_capture(knobs):
            holder["cur"] = knobs.get("ubatch-size", "512")
            load_fn(knobs)

        def measure_fn():
            return speeds[holder["cur"]]

        out = autotune.refine(base, "speed", load_capture, measure_fn,
                              budget_s=100, clock=FakeClock())
        self.assertEqual(out["knobs"]["ubatch-size"], "1024")
        self.assertEqual(out["measurements"]["chosen_tok_s"], 30.0)

    def test_falls_back_to_base_on_error(self):
        base = {"n-gpu-layers": "99"}

        def load_fn(knobs):
            raise RuntimeError("load failed")

        out = autotune.refine(base, "balanced", load_fn, lambda: 0.0,
                              budget_s=100, clock=FakeClock())
        self.assertEqual(out["knobs"], base)
        self.assertIn("load failed", out["error"])

    def test_one_measured_candidate_is_not_an_error(self):
        base = {"n-gpu-layers": "99", "ubatch-size": "512"}

        def load_fn(knobs):
            if knobs["ubatch-size"] == "1024":
                raise RuntimeError("out of memory")

        out = autotune.refine(base, "balanced", load_fn, lambda: 12.0,
                              budget_s=100, clock=FakeClock())
        self.assertNotIn("error", out)

    def test_respects_time_budget(self):
        base = {"n-gpu-layers": "99", "ubatch-size": "512"}
        calls = {"n": 0}

        def load_fn(knobs):
            calls["n"] += 1

        # each iteration advances the clock by 40s; budget 50s allows only one
        out = autotune.refine(base, "speed", load_fn, lambda: 5.0,
                              budget_s=50, clock=FakeClock(step=40.0))
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
