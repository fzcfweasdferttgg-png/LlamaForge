"""GPU telemetry without vendor tools: the kernel's DRM sysfs feeds the panel
and the multi-model planner, and device tokens follow whatever llama-server
prints (CUDA, Vulkan) - case included."""
import conftest_paths  # noqa: F401
import os, tempfile, unittest

import hardware, slots


def _mk_sysfs(root, cards):
    for card, files in cards.items():
        dev = os.path.join(root, card, "device")
        os.makedirs(dev)
        for name, content in files.items():
            if name == "hwmon_temp":
                h = os.path.join(dev, "hwmon", "hwmon3")
                os.makedirs(h)
                with open(os.path.join(h, "temp1_input"), "w") as f:
                    f.write(content)
            else:
                with open(os.path.join(dev, name), "w") as f:
                    f.write(content)


class SysfsGpusTest(unittest.TestCase):
    def test_reads_amd_cards_and_skips_the_bmc(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_sysfs(td, {
                "card0": {"vendor": "0x1002",
                          "mem_info_vram_total": str(16 * 1024 ** 3),
                          "mem_info_vram_used": str(4 * 1024 ** 3),
                          "gpu_busy_percent": "17", "hwmon_temp": "51000"},
                "card1": {"vendor": "0x1a03"},   # ASPEED BMC: nothing usable
            })
            gpus = hardware.sysfs_gpus(td)
        self.assertEqual(len(gpus), 1)
        g = gpus[0]
        self.assertEqual(g["index"], 0)
        self.assertEqual(g["total_mib"], 16 * 1024)
        self.assertEqual(g["used_mib"], 4 * 1024)
        self.assertEqual(g["free_mib"], 12 * 1024)
        self.assertEqual(g["util"], 17)
        self.assertEqual(g["temp"], 51)
        self.assertIn("AMD", g["name"])

    def test_intel_style_mem_info_also_works(self):
        with tempfile.TemporaryDirectory() as td:
            _mk_sysfs(td, {"card0": {"vendor": "0x8086",
                                     "mem_info_total": str(2 * 1024 ** 3),
                                     "mem_info_used": str(512 * 1024 ** 2)}})
            gpus = hardware.sysfs_gpus(td)
        self.assertEqual(gpus[0]["total_mib"], 2 * 1024)
        self.assertEqual(gpus[0]["used_mib"], 512)

    def test_empty_when_nothing_usable(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(hardware.sysfs_gpus(td), [])


VULKAN_LIST = """\
Vulkan0: AMD Radeon RX 9070 XT (RADV GFX1201) (16304 MiB, 16238 MiB free)
Vulkan1: AMD Radeon RX 9070 XT (RADV GFX1201) (16304 MiB, 16238 MiB free)
"""

CUDA_LIST = "CUDA0: NVIDIA GeForce RTX 4090 (sm_89, 24 GiB)\n"


class EnrichAndTokensTest(unittest.TestCase):
    def test_names_follow_the_engine_order(self):
        gpus = [{"index": 0, "name": "AMD (card0)"},
                {"index": 1, "name": "AMD (card1)"}]
        hardware.enrich_names(gpus, VULKAN_LIST)
        self.assertEqual([g["name"] for g in gpus],
                         ["AMD Radeon RX 9070 XT"] * 2)

    def test_vulkan_tokens_keep_their_case(self):
        gpus = [{"index": 0, "name": "AMD Radeon RX 9070 XT"},
                {"index": 1, "name": "AMD Radeon RX 9070 XT"}]
        cmap = slots.cuda_map(VULKAN_LIST, gpus)
        self.assertEqual(cmap.prefix, "Vulkan")
        self.assertEqual(dict(cmap), {0: 0, 1: 1})
        self.assertEqual(slots.cuda_name(1, cmap), "Vulkan1")

    def test_cuda_tokens_unchanged(self):
        gpus = [{"index": 0, "name": "NVIDIA GeForce RTX 4090"}]
        cmap = slots.cuda_map(CUDA_LIST, gpus)
        self.assertEqual(cmap.prefix, "CUDA")
        self.assertEqual(slots.cuda_name(0, cmap), "CUDA0")

    def test_pins_accept_vulkan_names(self):
        p = slots.pins({"device": "Vulkan0,Vulkan1"})
        self.assertEqual(p["devices"], [0, 1])

    def test_map_none_when_lists_disagree(self):
        self.assertIsNone(slots.cuda_map(VULKAN_LIST, [{"index": 0, "name": "x"}]))

    def test_memory_cross_check_guards_the_names(self):
        """The engine prints "(total MiB, ...)" per device: a slot whose totals
        disagree must keep its placeholder; agreeing totals take the name."""
        wrong = [{"index": 0, "name": "AMD (card0)", "total_mib": 24576}]
        hardware.enrich_names(wrong, VULKAN_LIST)
        self.assertEqual(wrong[0]["name"], "AMD (card0)")
        ok = [{"index": 0, "name": "AMD (card0)", "total_mib": 16304}]
        hardware.enrich_names(ok, VULKAN_LIST)
        self.assertEqual(ok[0]["name"], "AMD Radeon RX 9070 XT")


if __name__ == "__main__":
    unittest.main()
