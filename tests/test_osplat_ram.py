import conftest_paths  # noqa: F401
import unittest
import osplat


class TestTotalRam(unittest.TestCase):
    def test_returns_positive_or_zero(self):
        n = osplat.total_ram_bytes()
        self.assertIsInstance(n, int)
        self.assertGreaterEqual(n, 0)

    def test_parse_meminfo(self):
        text = "MemTotal:       32791234 kB\nMemFree: 100 kB\n"
        self.assertEqual(osplat.parse_meminfo(text), 32791234 * 1024)

    def test_parse_meminfo_missing(self):
        self.assertEqual(osplat.parse_meminfo("nope\n"), 0)


class TestAvailableRam(unittest.TestCase):
    def test_this_machine(self):
        n = osplat.available_ram_bytes()
        self.assertIsInstance(n, int)
        self.assertGreaterEqual(n, 0)
        total = osplat.total_ram_bytes()
        if n and total:
            self.assertLessEqual(n, total)

    def test_parse_meminfo_available(self):
        text = "MemTotal: 32000 kB\nMemFree: 100 kB\nMemAvailable:   20000 kB\n"
        self.assertEqual(osplat.parse_meminfo_available(text), 20000 * 1024)
        self.assertEqual(osplat.parse_meminfo_available("MemFree: 1 kB\n"), 0)


class TestNumaNodes(unittest.TestCase):
    SAMPLE = ("Node 0 MemTotal:       32768000 kB\n"
              "Node 0 MemFree:        12000000 kB\n"
              "Node 0 MemUsed:        20768000 kB\n")

    def test_parse_node_meminfo(self):
        d = osplat.parse_node_meminfo(self.SAMPLE)
        self.assertEqual(d["total"], 32768000 * 1024)
        self.assertEqual(d["used"], 20768000 * 1024)

    def test_used_falls_back_to_total_minus_free(self):
        text = "Node 1 MemTotal: 1000 kB\nNode 1 MemFree: 400 kB\n"
        d = osplat.parse_node_meminfo(text)
        self.assertEqual(d["total"], 1000 * 1024)
        self.assertEqual(d["used"], 600 * 1024)

    def test_parse_node_meminfo_without_node_lines(self):
        self.assertEqual(osplat.parse_node_meminfo("nope\n"),
                         {"total": 0, "used": 0})

    def test_nodes_are_consistent(self):
        nodes = osplat.numa_ram_nodes()
        self.assertIsInstance(nodes, list)
        for n in nodes:
            self.assertIn("id", n)
            self.assertGreater(n["total"], 0)
            self.assertGreaterEqual(n["total"], n["used"])
        if nodes:                     # a NUMA box adds up to about total RAM
            self.assertGreater(sum(n["total"] for n in nodes), 0)

    def test_node_names_are_numerically_ordered(self):
        self.assertEqual(osplat.node_names(["node10", "node2", "node0",
                                            "node1", "has_power"]),
                         ["node0", "node1", "node2", "node10"])

    def test_ram_telemetry_shape(self):
        r = osplat.ram_telemetry()
        self.assertGreaterEqual(r["total"], 0)
        self.assertGreaterEqual(r["used"], 0)
        self.assertGreaterEqual(r["available"], 0)
        self.assertIsInstance(r["nodes"], list)


if __name__ == "__main__":
    unittest.main()
