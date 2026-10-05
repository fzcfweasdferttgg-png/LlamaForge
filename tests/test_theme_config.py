import conftest_paths  # noqa: F401
import os, tempfile, unittest
import config


class TestThemeConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.dir, "config.json")

    def tearDown(self):
        config.CONFIG = self._orig

    def test_defaults(self):
        c = config.load()
        self.assertEqual(c["theme"], "")
        self.assertIs(c["cvd"], False)
        self.assertEqual(c["skin"], "")

    def test_round_trip(self):
        config.save({**config.load(), "theme": "light", "cvd": True})
        c = config.load()
        self.assertEqual(c["theme"], "light")
        self.assertIs(c["cvd"], True)

    def test_skin_round_trip(self):
        config.save({**config.load(), "skin": "classic"})
        self.assertEqual(config.load()["skin"], "classic")


if __name__ == "__main__":
    unittest.main()
