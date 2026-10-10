"""prebuilt.py's fork-engine helpers: ik_llama (Thireus) and beellama
(Anbeeld) asset names, CPU-ISA ladder and release resolution. Pure."""
import conftest_paths  # noqa: F401
import unittest

import prebuilt


IK_ASSETS = [
    {"name": "ik_llama-main-b5418-b210651-bin-ubuntu-vulkan-x64-avx2.zip",
     "state": "uploaded", "size": 1},
    {"name": "ik_llama-main-b5418-b210651-bin-ubuntu-vulkan-x64-avx512_vnni_bf16.zip",
     "state": "uploaded", "size": 1},
    {"name": "ik_llama-main-b5418-b210651-bin-ubuntu-cuda-12.8-x64-avx2.zip",
     "state": "uploaded", "size": 1},
    {"name": "ik_llama-cudart-main-b5418-b210651-bin-ubuntu-cuda-12.8-x64.zip",
     "state": "uploaded", "size": 1},
    {"name": "ik_llama-main-b5418-b210651-bin-macos-arm64.zip",
     "state": "uploaded", "size": 1},
]

BEE_ASSETS = [
    {"name": "beellama-v0.4.7-bin-ubuntu-vulkan-x64.tar.gz",
     "state": "uploaded", "size": 1},
    {"name": "beellama-v0.4.7-bin-ubuntu-x64.tar.gz", "state": "uploaded", "size": 1},
    {"name": "beellama-v0.4.7-bin-ubuntu-cuda-13.3-x64.tar.gz",
     "state": "uploaded", "size": 1},
    {"name": "beellama-v0.4.7-bin-ubuntu-arm64.tar.gz", "state": "uploaded", "size": 1},
    {"name": "beellama-v0.4.7-bin-win-cpu-x64.zip", "state": "uploaded", "size": 1},
]

PLAT = ("ubuntu", "x64")


class ParseForkAssetTest(unittest.TestCase):
    def test_ik_bin_carries_backend_and_isa(self):
        info = prebuilt.parse_fork_asset(
            "ikllama", "ik_llama-main-b5418-b210651-bin-ubuntu-vulkan-x64-avx512_vnni_bf16.zip")
        self.assertEqual(info, {"kind": "bin", "variant": "vulkan-avx512_vnni_bf16",
                                "cuda": None})

    def test_ik_cuda_and_cudart(self):
        b = prebuilt.parse_fork_asset(
            "ikllama", "ik_llama-main-b5418-b210651-bin-ubuntu-cuda-12.8-x64-avx2.zip")
        self.assertEqual(b["cuda"], (12, 8))
        c = prebuilt.parse_fork_asset(
            "ikllama", "ik_llama-cudart-main-b5418-b210651-bin-ubuntu-cuda-12.8-x64.zip")
        self.assertEqual(c["kind"], "cudart")

    def test_ik_only_ubuntu_x64(self):
        self.assertIsNone(prebuilt.parse_fork_asset(
            "ikllama", "ik_llama-main-b5418-b210651-bin-macos-arm64.zip"))

    def test_beellama_variants(self):
        self.assertEqual(prebuilt.parse_fork_asset(
            "beellama", "beellama-v0.4.7-bin-ubuntu-vulkan-x64.tar.gz")["variant"],
            "vulkan")
        self.assertEqual(prebuilt.parse_fork_asset(
            "beellama", "beellama-v0.4.7-bin-ubuntu-x64.tar.gz")["variant"], "cpu")
        self.assertIsNone(prebuilt.parse_fork_asset(
            "beellama", "beellama-v0.4.7-bin-ubuntu-arm64.tar.gz"))
        self.assertIsNone(prebuilt.parse_fork_asset(
            "beellama", "beellama-v0.4.7-bin-win-cpu-x64.zip"))


class PickIsaTest(unittest.TestCase):
    def test_ladder_picks_the_highest_supported(self):
        self.assertEqual(prebuilt.pick_isa(
            "avx2 avx512f avx512_vnni avx512_vbmi avx512_bf16"),
            "avx512_vnni_vbmi_bf16")
        self.assertEqual(prebuilt.pick_isa("avx2 avx512f avx512_vnni avx512_bf16"),
                         "avx512_vnni_bf16")
        self.assertEqual(prebuilt.pick_isa("avx2 avx512f avx512_vnni"), "avx512_vnni")
        self.assertEqual(prebuilt.pick_isa("avx2 avx512f"), "avx512")

    def test_baseline_and_other_arches(self):
        self.assertEqual(prebuilt.pick_isa("avx2"), "avx2")
        self.assertEqual(prebuilt.pick_isa("", arch="arm64"), "avx2")


class ChooseForkTest(unittest.TestCase):
    def test_manual_variant_wins(self):
        pick = prebuilt.choose_fork("beellama", BEE_ASSETS, PLAT, [], None,
                                   variant="cpu")
        self.assertEqual(pick["variant"], "cpu")
        self.assertEqual(pick["reason"], "chosen manually")

    def test_no_gpus_means_vulkan(self):
        pick = prebuilt.choose_fork("beellama", BEE_ASSETS, PLAT, [], None)
        self.assertEqual(pick["variant"], "vulkan")
        self.assertTrue(pick["bin"])

    def test_nvidia_gets_cuda_with_the_cudart(self):
        pick = prebuilt.choose_fork(
            "ikllama", IK_ASSETS, PLAT, [{"compute_cap": "8.9"}], (13, 3),
            flags="avx2")
        self.assertEqual(pick["variant"], "cuda-12.8-avx2")
        self.assertIsNotNone(pick["cudart"])

    def test_ik_prefers_the_highest_isa(self):
        pick = prebuilt.choose_fork(
            "ikllama", IK_ASSETS, PLAT, [], None,
            flags="avx2 avx512f avx512_vnni avx512_bf16")
        self.assertEqual(pick["variant"], "vulkan-avx512_vnni_bf16")

    def test_only_ubuntu(self):
        pick = prebuilt.choose_fork("beellama", BEE_ASSETS, ("win", "x64"), [], None)
        self.assertIsNone(pick["bin"])
        self.assertIn("Ubuntu", pick["reason"])

    def test_unuploaded_build_reports_it(self):
        assets = [{"name": "beellama-v0.4.7-bin-ubuntu-vulkan-x64.tar.gz",
                   "state": "starting"}]
        pick = prebuilt.choose_fork("beellama", assets, PLAT, [], None)
        self.assertIsNone(pick["bin"])
        self.assertIn("uploading", pick["reason"])


class ResolveReleaseTest(unittest.TestCase):
    def test_exact_tag(self):
        rel = {"tag_name": "v0.4.7", "published_at": "p", "assets": BEE_ASSETS}
        out = prebuilt.resolve_release(
            "beellama", tag="v0.4.7",
            get_json=lambda url: rel)
        self.assertEqual(out["tag"], "v0.4.7")

    def test_stable_beats_preview(self):
        releases = [
            {"tag_name": "preview-v9", "prerelease": True, "assets": BEE_ASSETS},
            {"tag_name": "v0.4.7", "prerelease": False, "assets": BEE_ASSETS},
        ]
        out = prebuilt.resolve_release(
            "beellama", get_json=lambda url: releases, plat=PLAT)
        self.assertEqual(out["tag"], "v0.4.7")

    def test_preview_used_when_that_is_all_there_is(self):
        releases = [{"tag_name": "preview-v9", "prerelease": True,
                     "assets": BEE_ASSETS}]
        out = prebuilt.resolve_release(
            "beellama", get_json=lambda url: releases, plat=PLAT)
        self.assertEqual(out["tag"], "preview-v9")

    def test_no_ubuntu_builds_raises(self):
        releases = [{"tag_name": "v1", "assets": [
            {"name": "beellama-v1-bin-win-cpu-x64.zip", "state": "uploaded"}]}]
        with self.assertRaises(RuntimeError):
            prebuilt.resolve_release("beellama", get_json=lambda url: releases,
                                     plat=PLAT)


if __name__ == "__main__":
    unittest.main()
