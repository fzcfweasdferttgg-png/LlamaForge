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


if __name__ == "__main__":
    unittest.main()
