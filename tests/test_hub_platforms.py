import conftest_paths  # noqa: F401
import unittest
import hub, routes


class TestPlatformTags(unittest.TestCase):
    def test_gguf_runs_everywhere(self):
        self.assertEqual(hub.PLATFORMS, ["windows", "linux", "macos"])


class TestInstalledRepos(unittest.TestCase):
    def test_gguf_repo_matched_via_download_folder_in_ini_path(self):
        results = [{"repo": "unsloth/Qwen3-8B-GGUF"}, {"repo": "other/Model-GGUF"}]
        ini = {"qwen3-8b": {"model": "D:/models/LlamaForge-downloads/unsloth--Qwen3-8B-GGUF/q4.gguf"}}
        inst = routes.installed_repos(results, ini)
        self.assertEqual(inst, ["unsloth/Qwen3-8B-GGUF"])

    def test_no_match(self):
        inst = routes.installed_repos([{"repo": "a/b"}], {"m": {"model": "c:/x.gguf"}})
        self.assertEqual(inst, [])


if __name__ == "__main__":
    unittest.main()
