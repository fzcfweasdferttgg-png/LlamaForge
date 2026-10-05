"""Which llama.cpp family can load a GGUF, from its tensor type ids."""
import conftest_paths  # noqa: F401
import os
import shutil
import tempfile
import unittest
from unittest import mock

import compat
import diag
from test_gguf_layout import kv_str, write_gguf

# The real Ternary-Bonsai-2-27B-PQ2_0.gguf: F32, BF16 and PrismML's PQ2_0.
BONSAI = {0, 30, 142}


class Classify(unittest.TestCase):
    def test_common_quants_run_anywhere(self):
        self.assertEqual(compat.classify({0, 1, 8, 12, 14}), "any")

    def test_mxfp4_is_shared(self):
        # gpt-oss / gemma mxfp4 builds: ik added 39 "so we are compatible with mainline"
        self.assertEqual(compat.classify({0, 39}), "any")

    def test_ik_quants(self):
        self.assertEqual(compat.classify(BONSAI), "ik-only")
        self.assertEqual(compat.classify({0, 12, 139}), "ik-only")       # IQ4_K
        self.assertEqual(compat.classify({0, 212}), "ik-only")           # Q4_K_R4
        self.assertEqual(compat.classify({0, 134}), "ik-only")           # IQ1_BN

    def test_ids_mainline_dropped_but_ik_kept(self):
        self.assertEqual(compat.classify({0, 31}), "ik-only")            # Q4_0_4_4
        self.assertEqual(compat.classify({0, 36}), "ik-only")            # ik's I2_S

    def test_mainline_quants(self):
        self.assertEqual(compat.classify({0, 34}), "mainline-only")      # TQ1_0
        self.assertEqual(compat.classify({0, 35}), "mainline-only")      # TQ2_0
        self.assertEqual(compat.classify({0, 40}), "mainline-only")      # NVFP4
        self.assertEqual(compat.classify({0, 42}), "mainline-only")      # Q2_0

    def test_one_id_two_meanings_is_unknown(self):
        # mainline Q1_0 and ik Q1_0_G128 share 41; don't guess which this is
        self.assertEqual(compat.classify({0, 41}), "unknown")

    def test_nobody_knows_it(self):
        self.assertEqual(compat.classify({0, 500}), "unknown")
        self.assertEqual(compat.classify({0, 142, 40}), "unknown")       # neither has both
        self.assertEqual(compat.classify({4}), "unknown")                # removed everywhere
        self.assertEqual(compat.classify(set()), "unknown")


class Names(unittest.TestCase):
    def test_names(self):
        self.assertEqual(compat.type_name(142), "PQ2_0")
        self.assertEqual(compat.type_name(12), "Q4_K")
        self.assertEqual(compat.type_name(40), "NVFP4")
        self.assertEqual(compat.type_name(999), "type 999")

    def test_deciding_types_are_the_ones_not_shared(self):
        self.assertEqual(compat.deciding(BONSAI), ["PQ2_0"])
        self.assertEqual(compat.deciding({0, 12}), [])


class ForModel(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        compat._CACHE.clear()

    def gguf(self, types, name="m.gguf"):
        p = os.path.join(self.dir, name)
        write_gguf(p, [kv_str("general.architecture", "llama")],
                   [(f"t{i}", t, 64) for i, t in enumerate(types)])
        return p

    def test_reads_the_file(self):
        out = compat.for_model(self.gguf([0, 30, 142, 142]))
        self.assertEqual(out, {"class": "ik-only", "types": ["PQ2_0"]})

    def test_unreadable_is_unknown(self):
        self.assertEqual(compat.for_model(os.path.join(self.dir, "gone.gguf")),
                         {"class": "unknown", "types": []})
        self.assertEqual(compat.for_model(""), {"class": "unknown", "types": []})

    def test_cached_until_the_file_changes(self):
        p = self.gguf([0, 12])
        with mock.patch.object(compat.gguf, "layout", wraps=compat.gguf.layout) as lay:
            compat.for_model(p)
            compat.for_model(p)
            self.assertEqual(lay.call_count, 1)
            self.gguf([0, 40, 40])                     # rewritten in place: new size
            self.assertEqual(compat.for_model(p)["class"], "mainline-only")
            self.assertEqual(lay.call_count, 2)


class Advice(unittest.TestCase):
    def test_on_llamacpp(self):
        self.assertIn("ik_llama.cpp", compat.advice("ik-only", ["PQ2_0"], "llamacpp"))
        self.assertIn("PQ2_0", compat.advice("ik-only", ["PQ2_0"], "llamacpp"))
        self.assertEqual(compat.advice("mainline-only", ["NVFP4"], "llamacpp"), "")

    def test_on_ik(self):
        self.assertIn("NVFP4", compat.advice("mainline-only", ["NVFP4"], "ikllama"))
        self.assertEqual(compat.advice("ik-only", ["PQ2_0"], "ikllama"), "")

    def test_nothing_to_say(self):
        self.assertEqual(compat.advice("any", [], "llamacpp"), "")
        self.assertEqual(compat.advice("unknown", [], "llamacpp"), "")


# Verbatim from this machine's llama-server on the Ternary-Bonsai file (2026-10-05).
PQ2_LOG = """\
srv  load_models: spawning server instance with name=bonsai on port 55001
[55001] 0.00.143.684 E gguf_init_from_reader: tensor 'output.weight' has invalid ggml type 142. should be in [0, 43)
[55001] 0.00.143.688 E gguf_init_from_reader: failed to read tensor info
[55001] 0.00.149.600 E llama_model_load: error loading model: llama_model_loader: failed to load model from D:/m/Bonsai-PQ2_0.gguf
[55001] 0.00.197.563 E srv  llama_server: exiting due to model loading error
srv  operator(): instance name=bonsai exited with status 1
"""


class Diag(unittest.TestCase):
    def test_unknown_quant_type_names_it(self):
        d = diag.diagnose(PQ2_LOG, {}, model="bonsai")
        self.assertIn("invalid ggml type 142", d["error"])
        self.assertIn("PQ2_0", d["suggestion"])
        self.assertIn("ik_llama.cpp", d["suggestion"])

    def test_a_type_nobody_defines(self):
        d = diag.diagnose(PQ2_LOG.replace("type 142", "type 777"), {}, model="bonsai")
        self.assertIn("type 777", d["suggestion"])
        self.assertNotIn("ik_llama.cpp can", d["suggestion"])


class Route(unittest.TestCase):
    """The editor's GGUF card carries the verdict: read when a row opens,
    never on the dashboard's poll."""
    def setUp(self):
        import routes
        self.routes = routes
        self.cfg = {"active_engine": "llamacpp"}
        mock.patch.object(routes, "cfg", side_effect=lambda: dict(self.cfg)).start()
        mock.patch.object(routes.config, "read_sections",
                          return_value={"bonsai": {"model": "D:/m/b.gguf"}}).start()
        mock.patch.object(routes.gguf, "metadata", return_value={"architecture": "qwen3"}).start()
        self.for_model = mock.patch.object(routes.compat, "for_model",
                                           return_value={"class": "ik-only", "types": ["PQ2_0"]}).start()
        self.addCleanup(mock.patch.stopall)

    def test_metadata_carries_the_verdict_and_advice(self):
        status, out = self.routes.get_model_metadata(self.routes.Req(qs={"model": "bonsai"}))
        self.assertEqual(out["metadata"], {"architecture": "qwen3"})
        self.assertEqual((out["compat"]["class"], out["compat"]["types"]), ("ik-only", ["PQ2_0"]))
        self.assertIn("ik_llama.cpp", out["compat"]["advice"])
        self.for_model.assert_called_once_with("D:/m/b.gguf")

    def test_on_ik_an_ik_quant_needs_no_advice(self):
        self.cfg["active_engine"] = "ikllama"
        status, out = self.routes.get_model_metadata(self.routes.Req(qs={"model": "bonsai"}))
        self.assertEqual(out["compat"]["advice"], "")

    def test_unknown_model(self):
        status, out = self.routes.get_model_metadata(self.routes.Req(qs={"model": "nope"}))
        self.assertEqual(out, {"metadata": {}, "compat": {"class": "unknown", "types": [], "advice": ""}})


if __name__ == "__main__":
    unittest.main()
