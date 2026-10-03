import conftest_paths  # noqa: F401
import os, shutil, tempfile, unittest

import config
import embers


class EmbersConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.dir, "config.json")

    def tearDown(self):
        config.CONFIG = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_is_repo_embers_folder(self):
        self.assertEqual(config.DEFAULTS["embers_dir"], "")
        self.assertEqual(os.path.normpath(embers.embers_dir()),
                         os.path.normpath(os.path.join(config.ROOT, "embers")))

    def test_configured_absolute_path_wins(self):
        target = os.path.join(self.dir, "my-embers")
        config.update({"embers_dir": target})
        self.assertEqual(os.path.normpath(embers.embers_dir()), os.path.normpath(target))

    def test_relative_path_is_anchored_to_repo(self):
        self.assertEqual(os.path.normpath(embers.embers_dir({"embers_dir": "data/embers"})),
                         os.path.normpath(os.path.join(config.ROOT, "data/embers")))

    def test_gitignore_ignores_data_but_not_the_package(self):
        with open(os.path.join(config.ROOT, ".gitignore"), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        self.assertIn("/embers/", lines)
        self.assertNotIn("embers/", lines)   # would also hide backend/embers/


if __name__ == "__main__":
    unittest.main()
