import conftest_paths  # noqa: F401
import json
import os
import tempfile
import unittest

import footprint
import slots

M = 1024 * 1024
OH = slots.OVERHEAD_MIB


def dense(n_layer=10, layer_mib=500, tied=False, **kv):
    tensors = [("token_embd.weight", 14, 1000 * M)]
    for i in range(n_layer):
        tensors.append((f"blk.{i}.attn_q.weight", 14, layer_mib * M))
    if not tied:
        tensors.append(("output.weight", 14, 1000 * M))
    base = {"block_count": n_layer, "embedding_length": 4096, "head_count": 32,
            "head_count_kv": 8, "context_length": 32768}
    base.update(kv)
    return {"arch": "llama", "kv": base, "tensors": tensors}


def moe(n_layer=4):
    tensors = [("token_embd.weight", 14, 500 * M), ("output.weight", 14, 500 * M)]
    for i in range(n_layer):
        tensors += [(f"blk.{i}.attn_q.weight", 14, 100 * M),
                    (f"blk.{i}.ffn_up_exps.weight", 14, 1000 * M),
                    (f"blk.{i}.ffn_down_exps.weight", 14, 1000 * M)]
    return {"arch": "qwen3moe", "tensors": tensors,
            "kv": {"block_count": n_layer, "embedding_length": 2048, "head_count": 32,
                   "head_count_kv": 4, "key_length": 128, "value_length": 128,
                   "context_length": 32768}}


# 10 layers x 8192 tokens x 8 kv heads x (128 + 128) x 2 bytes = 320 MiB
KV_8K = 320


class Predict(unittest.TestCase):
    def test_dense_full_offload(self):
        p = footprint.predict({"ctx-size": "8192"}, dense())
        self.assertEqual(p["need_mib"], 10 * 500 + 1000 + KV_8K + OH)
        self.assertEqual(p["ram_mib"], 1000)          # the input embedding stays on the CPU
        self.assertTrue(p["confident"])

    def test_tied_output_copies_the_embedding_to_the_gpu(self):
        p = footprint.predict({"ctx-size": "8192"}, dense(tied=True))
        self.assertEqual(p["need_mib"], 10 * 500 + 1000 + KV_8K + OH)
        self.assertEqual(p["ram_mib"], 1000)

    def test_partial_offload_takes_the_last_layers(self):
        p = footprint.predict({"ctx-size": "8192", "n-gpu-layers": "5", "no-op-offload": "true"},
                              dense())
        self.assertEqual(p["need_mib"], 5 * 500 + KV_8K // 2 + OH)
        self.assertEqual(p["ram_mib"], 1000 + 1000 + 5 * 500 + KV_8K // 2)

    def test_cpu_layers_need_a_staging_copy_on_the_gpu(self):
        # op offload copies a CPU layer's weights to the GPU for prompt batches;
        # measured on gemma-4-26b-a4b with n-cpu-moe 10, missing it under-predicts
        p = footprint.predict({"ctx-size": "8192", "n-gpu-layers": "5"}, dense())
        self.assertEqual(p["staging_mib"], 500)
        self.assertEqual(p["need_mib"], 5 * 500 + KV_8K // 2 + 500 + OH)
        p = footprint.predict({"ctx-size": "8192", "n-cpu-moe": "1"}, moe())
        self.assertEqual(p["staging_mib"], 2000)
        p = footprint.predict({"ctx-size": "8192", "op-offload": "false", "n-cpu-moe": "1"}, moe())
        self.assertEqual(p["staging_mib"], 0)

    def test_full_offload_needs_no_staging(self):
        self.assertEqual(footprint.predict({"ctx-size": "8192"}, dense())["staging_mib"], 0)
        self.assertEqual(footprint.predict({"ctx-size": "8192", "device": "none"},
                                           dense())["staging_mib"], 0)

    def test_all_and_auto_mean_everything(self):
        for v in ("99", "999", "-1", "all", "auto"):
            p = footprint.predict({"ctx-size": "8192", "n-gpu-layers": v}, dense())
            self.assertEqual(p["need_mib"], 6000 + KV_8K + OH, v)

    def test_ngl_alias(self):
        p = footprint.predict({"ctx-size": "8192", "ngl": "5"}, dense())
        self.assertEqual(p["need_mib"], 5 * 500 + KV_8K // 2 + 500 + OH)

    def test_cpu_moe_keeps_every_expert_in_ram(self):
        p = footprint.predict({"ctx-size": "8192", "cpu-moe": "true"}, moe())
        self.assertEqual(p["weights_gpu_mib"], 4 * 100 + 500)
        self.assertEqual(p["weights_ram_mib"], 500 + 4 * 2000)

    def test_n_cpu_moe_keeps_the_first_layers_experts_in_ram(self):
        p = footprint.predict({"ctx-size": "8192", "n-cpu-moe": "1"}, moe())
        self.assertEqual(p["weights_gpu_mib"], 4 * 100 + 500 + 3 * 2000)
        self.assertEqual(p["weights_ram_mib"], 500 + 2000)

    def test_override_tensor_to_cpu(self):
        p = footprint.predict({"ctx-size": "8192",
                               "override-tensor": r"blk\.[0-1]\.ffn_down_exps=CPU"}, moe())
        self.assertEqual(p["weights_ram_mib"], 500 + 2 * 1000)
        self.assertTrue(p["confident"])

    def test_unparseable_override_is_not_confident(self):
        p = footprint.predict({"ctx-size": "8192", "override-tensor": "blk.(=CPU"}, moe())
        self.assertFalse(p["confident"])

    def test_quantized_kv(self):
        p = footprint.predict({"ctx-size": "8192", "cache-type-k": "q8_0",
                               "cache-type-v": "q8_0"}, dense())
        self.assertEqual(p["kv_gpu_mib"], 170)        # 320 at 2 bytes -> 34/32 bytes

    def test_hybrid_only_attention_layers_hold_kv(self):
        p = footprint.predict({"ctx-size": "8192"}, dense(head_count_kv=[0, 8] * 5))
        self.assertEqual(p["kv_gpu_mib"], KV_8K // 2)

    def test_full_attention_interval(self):
        p = footprint.predict({"ctx-size": "8192"},
                              dense(n_layer=8, full_attention_interval=4))
        self.assertEqual(p["kv_gpu_mib"], 2 * 32)     # 2 of 8 layers, 32 MiB each

    def test_sliding_window_layers_cache_only_the_window(self):
        lay = dense(n_layer=12, sliding_window=1024)
        lay["arch"] = "gemma3"                        # every 6th layer is global
        p = footprint.predict({"ctx-size": "32768", "parallel": "1"}, lay)
        per_tok_layer = 8 * 256 * 2                   # bytes
        want = (2 * 32768 + 10 * (1024 + 512)) * per_tok_layer // M
        self.assertEqual(p["kv_gpu_mib"], want)

    def test_swa_full_caches_everything(self):
        lay = dense(n_layer=12, sliding_window=1024)
        lay["arch"] = "gemma3"
        p = footprint.predict({"ctx-size": "32768", "swa-full": "true"}, lay)
        self.assertEqual(p["kv_gpu_mib"], 12 * 32768 * 8 * 256 * 2 // M)

    def test_swa_pattern_from_the_file(self):
        lay = dense(n_layer=4, sliding_window=1024,
                    sliding_window_pattern=[True, False, True, False])
        p = footprint.predict({"ctx-size": "32768", "parallel": "1"}, lay)
        self.assertEqual(p["kv_gpu_mib"], (2 * 32768 + 2 * 1536) * 4096 // M)

    def test_ctx_defaults_to_the_trained_context(self):
        for s in ({}, {"ctx-size": "0"}):
            p = footprint.predict(s, dense())
            self.assertEqual(p["ctx"], 32768)
            self.assertEqual(p["kv_gpu_mib"], KV_8K * 4)

    def test_c_alias(self):
        self.assertEqual(footprint.predict({"c": "8192"}, dense())["ctx"], 8192)

    def test_no_kv_offload(self):
        p = footprint.predict({"ctx-size": "8192", "no-kv-offload": "true"}, dense())
        self.assertEqual(p["kv_gpu_mib"], 0)
        self.assertEqual(p["kv_ram_mib"], KV_8K)

    def test_cpu_only(self):
        p = footprint.predict({"ctx-size": "8192", "device": "none"}, dense())
        self.assertEqual(p["need_mib"], 0)
        self.assertEqual(p["ram_mib"], 1000 + 6000 + KV_8K)

    def test_a_draft_model_goes_with_the_model(self):
        p = footprint.predict({"ctx-size": "8192"}, dense(), extra_gpu_bytes=800 * M)
        self.assertEqual(p["need_mib"], 6000 + KV_8K + 800 + OH)

    def test_the_projector_goes_to_the_first_gpu(self):
        # measured: gemma-4-12b / qwen3.8-27b pinned to CUDA1 put their mmproj
        # (167 / 884 MiB) plus a 177 / 252 MiB compute buffer on CUDA0
        p = footprint.predict({"ctx-size": "8192"}, dense(), mmproj_bytes=884 * M)
        self.assertEqual(p["need_mib"], 6000 + KV_8K + OH)
        self.assertEqual(p["first_gpu_mib"], 884 + footprint.MMPROJ_COMPUTE_MIB)
        self.assertGreaterEqual(footprint.MMPROJ_COMPUTE_MIB, 252)

    def test_a_projector_kept_on_the_cpu(self):
        for s in ({"mmproj-offload": "false"}, {"no-mmproj-offload": "true"},
                  {"device": "none"}):
            p = footprint.predict(dict(s, **{"ctx-size": "8192"}), dense(), mmproj_bytes=884 * M)
            self.assertEqual(p["first_gpu_mib"], 0, s)
            self.assertGreaterEqual(p["ram_mib"], 1000 + 884, s)

    def test_no_layout_still_places_the_projector(self):
        p = footprint.predict({"ctx-size": "8192"}, None, size_bytes=5000 * M, mmproj_bytes=884 * M)
        self.assertEqual(p["first_gpu_mib"], 884 + footprint.MMPROJ_COMPUTE_MIB)

    def test_no_layout_falls_back_to_file_size(self):
        p = footprint.predict({"ctx-size": "8192"}, None, size_bytes=5000 * M)
        self.assertFalse(p["confident"])
        self.assertGreater(p["need_mib"], 5000 + OH)


class Key(unittest.TestCase):
    def test_sampling_does_not_change_the_key(self):
        a = footprint.key("m", "b1", {"ctx-size": "8192", "temp": "0.7"})
        b = footprint.key("m", "b1", {"ctx-size": "8192", "temp": "0.2", "top-k": "20"})
        self.assertEqual(a, b)

    def test_memory_settings_do(self):
        a = footprint.key("m", "b1", {"ctx-size": "8192"})
        for s in ({"ctx-size": "16384"}, {"ctx-size": "8192", "cache-type-k": "q8_0"},
                  {"ctx-size": "8192", "n-cpu-moe": "4"}, {"ctx-size": "8192", "mmproj": "x"}):
            self.assertNotEqual(a, footprint.key("m", "b1", s), s)
        self.assertNotEqual(a, footprint.key("m", "b2", {"ctx-size": "8192"}))

    def test_placement_is_not_part_of_the_key(self):
        a = footprint.key("m", "b1", {"ctx-size": "8192"})
        b = footprint.key("m", "b1", {"ctx-size": "8192", "device": "CUDA1",
                                      "split-mode": "none", "main-gpu": "1"})
        self.assertEqual(a, b)


class Store(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "footprints.json")
        self.s = footprint.Store(self.path)

    def test_record_and_read_back(self):
        self.s.record("k", [1], {1: 5000, 0: 231}, when="load")
        self.assertEqual(self.s.measured("k"), {(1,): {1: 5000, 0: 231}})
        self.assertEqual(footprint.Store(self.path).measured("k"), {(1,): {1: 5000, 0: 231}})

    def test_unload_keeps_the_larger_reading(self):
        # the CUDA pool grows on the first long prompt; what unload frees is the truth
        self.s.record("k", [0], {0: 5000}, when="load")
        self.s.record("k", [0], {0: 5600}, when="unload")
        self.s.record("k", [0], {0: 5100}, when="unload")
        self.assertEqual(self.s.measured("k"), {(0,): {0: 5600}})

    def test_a_new_load_replaces(self):
        self.s.record("k", [0], {0: 5600}, when="unload")
        self.s.record("k", [0], {0: 4000}, when="load")
        self.assertEqual(self.s.measured("k"), {(0,): {0: 4000}})

    def test_noise_is_dropped(self):
        self.s.record("k", [0], {0: 5000, 1: 12}, when="load")
        self.assertEqual(self.s.measured("k"), {(0,): {0: 5000}})

    def test_nothing_measured_records_nothing(self):
        self.s.record("k", [0], {0: 3}, when="load")
        self.assertEqual(self.s.measured("k"), {})

    def test_corrupt_file_is_empty(self):
        with open(self.path, "w") as f:
            f.write("{nope")
        self.assertEqual(footprint.Store(self.path).measured("k"), {})

    def test_file_is_plain_json(self):
        self.s.record("k", [0, 1], {0: 9000, 1: 9000}, when="load")
        with open(self.path) as f:
            self.assertEqual(json.load(f)["k"]["0,1"], {"0": 9000, "1": 9000})


if __name__ == "__main__":
    unittest.main()
