import conftest_paths  # noqa: F401
import os, tempfile, unittest

import bootstrap_config


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8"):
        pass
    return path


class BootstrapConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "existing llama.cpp")
        os.makedirs(self.src)

    def test_custom_source_drops_the_build_keys(self):
        cfg = bootstrap_config.build(self.tmp, self.src)
        self.assertNotIn("llama_src", cfg)
        self.assertNotIn("build_dir", cfg)
        self.assertNotIn("cmake_flags", cfg)
        self.assertNotIn("git_remote", cfg)
        self.assertIn("server_bin", cfg)

    def test_detects_existing_release_binary(self):
        want = _touch(os.path.join(self.src, "build", "bin", "Release",
                                   "llama-server.exe"))
        self.assertEqual(bootstrap_config.build(self.tmp, self.src)["server_bin"], want)

    def test_detects_existing_flat_binary(self):
        want = _touch(os.path.join(self.src, "build", "bin", "llama-server"))
        self.assertEqual(bootstrap_config.build(self.tmp, self.src)["server_bin"], want)

    def test_unbuilt_tree_gets_platform_appropriate_guess(self):
        cfg = bootstrap_config.build(self.tmp, self.src, windows=True)
        self.assertEqual(cfg["server_bin"], os.path.join(
            os.path.abspath(self.src), "build", "bin", "Release", "llama-server.exe"))


if __name__ == "__main__":
    unittest.main()
