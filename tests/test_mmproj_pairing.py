"""Pair same-folder mmproj projectors with their model (Discussion #16).

The old gate was a text-architecture allowlist that named clip projector types
instead of model archs, so gemma4 / qwen35 / qwen3vl models never got their
projector. Pairing now rests on the folder layout plus the projector's own
header: clip.*.projection_dim must equal the model's embedding_length, which is
the same check llama.cpp makes when it loads the projector.
"""
import conftest_paths  # noqa: F401
import os, struct, tempfile, unittest
from unittest import mock

import gguf, scanner


def _entries(paths, models, projectors):
    """build_entries over fake paths. `models` maps path -> metadata dict,
    `projectors` maps path -> projector_info dict (missing -> unreadable)."""
    with mock.patch("gguf.metadata", side_effect=lambda p: models.get(p)), \
         mock.patch("gguf.projector_info", side_effect=lambda p: projectors.get(p), create=True), \
         mock.patch("gguf.has_nextn", return_value=False), \
         mock.patch.object(scanner, "total_size", return_value=0):
        return {e["model"]: e for e in scanner.build_entries(paths)}


GEMMA4 = {"architecture": "gemma4", "embedding_length": 3840}
QWEN35 = {"architecture": "qwen35", "embedding_length": 2560}
LLAMA  = {"architecture": "llama", "embedding_length": 4096}
P3840 = {"projection_dim": 3840}
P2560 = {"projection_dim": 2560}


class SingleModelFolderTest(unittest.TestCase):
    def test_gemma4_gets_its_projector(self):
        m, p = "/m/g/gemma-4-31B-it-Q6_K.gguf", "/m/g/mmproj-F16.gguf"
        e = _entries([m, p], {m: GEMMA4}, {p: P3840})
        self.assertEqual(list(e), [m])              # projector isn't a model entry
        self.assertEqual(e[m]["mmproj"], p)

    def test_qwen35_gets_its_projector(self):
        m, p = "/m/q/Qwen3.5-4B.Q6_K.gguf", "/m/q/mmproj-BF16.gguf"
        e = _entries([m, p], {m: QWEN35}, {p: P2560})
        self.assertEqual(e[m]["mmproj"], p)

    def test_dimension_mismatch_not_attached(self):
        m, p = "/m/g/gemma-4-31B-it-Q6_K.gguf", "/m/g/mmproj-F16.gguf"
        e = _entries([m, p], {m: GEMMA4}, {p: P2560})
        self.assertNotIn("mmproj", e[m])

    def test_unreadable_projector_not_attached(self):
        m, p = "/m/g/gemma-4-31B-it-Q6_K.gguf", "/m/g/mmproj-F16.gguf"
        e = _entries([m, p], {m: GEMMA4}, {})
        self.assertNotIn("mmproj", e[m])

    def test_projector_in_other_folder_not_attached(self):
        m, p = "/m/a/gemma-4-31B-it-Q6_K.gguf", "/m/b/mmproj-F16.gguf"
        e = _entries([m, p], {m: GEMMA4}, {p: P3840})
        self.assertNotIn("mmproj", e[m])


class SharedFolderTest(unittest.TestCase):
    def test_generic_projector_with_two_unrelated_models_not_attached(self):
        a, b = "/m/s/gemma-4-31B-it-Q6_K.gguf", "/m/s/Llama-3-8B-Q8_0.gguf"
        p = "/m/s/mmproj-F16.gguf"
        e = _entries([a, b, p], {a: GEMMA4, b: LLAMA}, {p: P3840})
        self.assertNotIn("mmproj", e[a])
        self.assertNotIn("mmproj", e[b])

    def test_named_projector_attaches_only_to_its_model(self):
        a, b = "/m/s/Swift-Qwen3.8-27B-Q6_K.gguf", "/m/s/Llama-3-8B-Q8_0.gguf"
        p = "/m/s/mmproj-Swift-Qwen3.8-27B-F16.gguf"
        e = _entries([a, b, p], {a: QWEN35, b: LLAMA}, {p: P2560})
        self.assertEqual(e[a]["mmproj"], p)
        self.assertNotIn("mmproj", e[b])

    def test_several_quants_of_one_model_share_generic_projector(self):
        a, b = "/m/q/Qwen3.5-4B-Q4_K_M.gguf", "/m/q/Qwen3.5-4B-UD-IQ2_XXS.gguf"
        p = "/m/q/mmproj-F16.gguf"
        e = _entries([a, b, p], {a: QWEN35, b: QWEN35}, {p: P2560})
        self.assertEqual(e[a]["mmproj"], p)
        self.assertEqual(e[b]["mmproj"], p)

    def test_mtp_sidecar_does_not_block_pairing(self):
        m, t = "/m/g/gemma-4-12b-it-Q6_K.gguf", "/m/g/mtp-gemma-4-12b-it.gguf"
        p = "/m/g/mmproj-F16.gguf"
        e = _entries([m, t, p], {m: GEMMA4}, {p: P3840})
        self.assertEqual(e[m]["mmproj"], p)


class SuffixNamedProjectorTest(unittest.TestCase):
    """Some repos name the projector <model>-mmproj-BF16.gguf (prism-ml Bonsai)."""

    def test_suffix_named_projector_is_paired_not_listed(self):
        m = "/m/b/Ternary-Bonsai-2-27B-PQ2_0.gguf"
        p = "/m/b/Ternary-Bonsai-2-27B-mmproj-BF16.gguf"
        e = _entries([m, p], {m: {"architecture": "qwen35", "embedding_length": 5120}},
                     {p: {"projection_dim": 5120}})
        self.assertEqual(list(e), [m])
        self.assertEqual(e[m]["mmproj"], p)

    def test_word_containing_mmproj_is_not_a_projector(self):
        self.assertFalse(scanner._is_mmproj("/m/notmmprojector-7B-Q4_K_M.gguf"))


class SeveralProjectorsTest(unittest.TestCase):
    def _pick(self, names):
        m = "/m/g/gemma-4-12b-it-Q6_K.gguf"
        projs = ["/m/g/" + n for n in names]
        e = _entries([m] + projs, {m: GEMMA4}, {p: P3840 for p in projs})
        return os.path.basename(e[m]["mmproj"])

    def test_f16_preferred_over_f32_and_bf16(self):
        self.assertEqual(self._pick(["mmproj-F32.gguf", "mmproj-BF16.gguf", "mmproj-F16.gguf"]),
                         "mmproj-F16.gguf")
        # order of discovery must not matter
        self.assertEqual(self._pick(["mmproj-F16.gguf", "mmproj-F32.gguf"]), "mmproj-F16.gguf")

    def test_bf16_preferred_over_f32(self):
        self.assertEqual(self._pick(["mmproj-F32.gguf", "mmproj-BF16.gguf"]), "mmproj-BF16.gguf")

    def test_otherwise_name_order(self):
        self.assertEqual(self._pick(["mmproj-Q8_0.gguf", "mmproj-Q4_K_M.gguf"]),
                         "mmproj-Q4_K_M.gguf")

    def test_matching_dimension_beats_precision(self):
        m = "/m/g/gemma-4-12b-it-Q6_K.gguf"
        good, bad = "/m/g/mmproj-F32.gguf", "/m/g/mmproj-F16.gguf"
        e = _entries([m, good, bad], {m: GEMMA4}, {good: P3840, bad: P2560})
        self.assertEqual(e[m]["mmproj"], good)


class ProjectorInfoTest(unittest.TestCase):
    """gguf.projector_info reads the clip header of a real file."""

    def _write(self, arch, kvs):
        path = os.path.join(tempfile.mkdtemp(), "mmproj.gguf")
        with open(path, "wb") as f:
            f.write(b"GGUF"); f.write(struct.pack("<I", 3))
            f.write(struct.pack("<Q", 0)); f.write(struct.pack("<Q", len(kvs) + 1))
            def key(k):
                kb = k.encode(); f.write(struct.pack("<Q", len(kb))); f.write(kb)
            key("general.architecture"); f.write(struct.pack("<I", 8))   # string
            ab = arch.encode(); f.write(struct.pack("<Q", len(ab))); f.write(ab)
            for k, v in kvs:
                key(k); f.write(struct.pack("<I", 4)); f.write(struct.pack("<I", v))
        return path

    def test_vision_projection_dim(self):
        p = self._write("clip", [("clip.vision.projection_dim", 3840)])
        self.assertEqual(gguf.projector_info(p), {"projection_dim": 3840})

    def test_audio_only_projection_dim(self):
        p = self._write("clip", [("clip.audio.projection_dim", 2048)])
        self.assertEqual(gguf.projector_info(p), {"projection_dim": 2048})

    def test_non_clip_file_is_none(self):
        p = self._write("llama", [("llama.embedding_length", 4096)])
        self.assertIsNone(gguf.projector_info(p))

    def test_unreadable_is_none(self):
        self.assertIsNone(gguf.projector_info(os.path.join(tempfile.mkdtemp(), "nope.gguf")))


if __name__ == "__main__":
    unittest.main()
