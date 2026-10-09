import conftest_paths  # noqa: F401
import os, tempfile, threading, unittest
from unittest import mock
import urllib.request
import hub, routes


class FakeResp:
    def __init__(self, status, body, clen=None):
        self.status = status
        self._body = body
        self._sent = False
        self.headers = {"Content-Length": str(clen if clen is not None else len(body))}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n):
        if self._sent:
            return b""
        self._sent = True
        return self._body


class TestResume(unittest.TestCase):
    def _patch(self, resp, capture):
        def fake(req, *a, **k):
            capture["range"] = req.get_header("Range")
            return resp
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake

    def tearDown(self):
        if hasattr(self, "_orig"):
            urllib.request.urlopen = self._orig

    def test_resume_sends_range_and_appends(self):
        dm = hub.DownloadManager()
        cap = {}
        self._patch(FakeResp(206, b"BBBBB", clen=5), cap)
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "m.gguf")
            with open(dest + ".part", "wb") as f:
                f.write(b"AAAAA")           # 5 bytes already fetched
            dm._fetch("http://x", dest)
            self.assertEqual(cap["range"], "bytes=5-")
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"AAAAABBBBB")
            self.assertFalse(os.path.exists(dest + ".part"))
            self.assertEqual(dm.state["total"], 10)

    def test_range_ignored_restarts_from_scratch(self):
        dm = hub.DownloadManager()
        cap = {}
        self._patch(FakeResp(200, b"CCCCCCC", clen=7), cap)
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "m.gguf")
            with open(dest + ".part", "wb") as f:
                f.write(b"AAAAA")           # stale partial; server ignored Range
            dm._fetch("http://x", dest)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"CCCCCCC")
            self.assertEqual(dm.state["total"], 7)
            self.assertEqual(dm.state["downloaded"], 7)

    def test_pause_keeps_part_and_can_resume(self):
        dm = hub.DownloadManager()
        started = threading.Event()

        def fake_fetch(url, dest):
            open(dest + ".part", "wb").close()   # a partial exists
            started.set()
            while True:
                dm._check_signals()
        dm._fetch = fake_fetch

        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(dm.start("r/x", ["a.gguf"], d))
            started.wait(5)
            self.assertTrue(dm.pause())
            for _ in range(50):
                if not dm.state["running"]:
                    break
                threading.Event().wait(0.05)
            self.assertFalse(dm.state["running"])
            self.assertEqual(dm.state["phase"], "paused")
            self.assertTrue(os.path.exists(os.path.join(d, "a.gguf.part")))
            # resume should relaunch the same job
            started.clear()
            self.assertTrue(dm.resume())
            started.wait(5)
            self.assertTrue(dm.state["running"])
            dm.cancel()

    def test_resume_refused_when_no_paused_job(self):
        self.assertFalse(hub.DownloadManager().resume())

    def test_truncated_body_raises_and_keeps_partial(self):
        # FakeResp's one-shot read() then b"" is exactly how a dropped
        # connection looks to the chunk loop: the file must not be renamed.
        dm = hub.DownloadManager()
        self._patch(FakeResp(200, b"AAAAA", clen=10), {})   # 5 of 10 bytes
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "m.gguf")
            with self.assertRaises(IOError):
                dm._fetch("http://x", dest)
            self.assertFalse(os.path.exists(dest))
            self.assertEqual(os.path.getsize(dest + ".part"), 5)

    def test_truncation_reports_failed_not_done(self):
        dm = hub.DownloadManager()
        self._patch(FakeResp(200, b"AAAAA", clen=10), {})
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(dm.start("r/x", ["m.gguf"], d))
            for _ in range(50):
                if not dm.state["running"]:
                    break
                threading.Event().wait(0.05)
            st = dm.progress()
            self.assertEqual(st["phase"], "failed")
            self.assertIn("truncated", st["error"])
            self.assertFalse(os.path.exists(os.path.join(d, "m.gguf")))


class HubMtpSidecarTest(unittest.TestCase):
    def test_files_separates_mtp_and_pairs_only_the_matching_main(self):
        tree = [
            {"path": "alpha-q4.gguf", "size": 4},
            {"path": "beta-q4.gguf", "size": 5},
            {"path": "mtp-beta-q4.gguf", "size": 1},
        ]
        with mock.patch.object(hub, "_get_json", return_value=tree):
            result = hub.files("owner/repo")

        self.assertEqual(result["mtp"],
                         [{"path": "mtp-beta-q4.gguf", "size": 1}])
        choices = {f["path"]: f for f in result["files"]}
        self.assertNotIn("mtp", choices["alpha-q4.gguf"])
        self.assertEqual(choices["beta-q4.gguf"]["mtp"], "mtp-beta-q4.gguf")

    def test_download_includes_selected_mtp_sidecar(self):
        seen = {}

        def start(repo, paths, dest):
            seen.update(repo=repo, paths=paths, dest=dest)
            return True

        req = mock.Mock()
        req.body = {"repo": "owner/repo", "path": "beta-q4.gguf",
                    "shards": 1, "mtp": "mtp-beta-q4.gguf"}
        with mock.patch.object(routes.DOWNLOADS, "start", side_effect=start), \
             mock.patch.object(routes, "download_dir", return_value="/downloads"):
            status, result = routes.post_hub_download(req)

        self.assertEqual(status, 200)
        self.assertTrue(result["started"])
        self.assertEqual(seen["paths"], ["beta-q4.gguf", "mtp-beta-q4.gguf"])


if __name__ == "__main__":
    unittest.main()
