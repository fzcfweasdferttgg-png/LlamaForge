import conftest_paths  # noqa: F401
import os, shutil, subprocess, tempfile, unittest
import builder


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args],
                       cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


class TestUpstreamBranchResolution(unittest.TestCase):
    """Real repos, not stubs: the bug was git's own behaviour (a checkout whose
    origin/HEAD is origin/main has no origin/master, so rev-list failed)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bm = builder.BuildManager(os.path.join(self.tmp, "logs"))

    def make_pair(self, branch):
        """An upstream repo on `branch`, plus a clone of it one commit behind."""
        up = os.path.join(self.tmp, f"up-{branch}")
        os.makedirs(up)
        git(up, "init", "-b", branch)
        git(up, "commit", "--allow-empty", "-m", "first")
        clone = os.path.join(self.tmp, f"clone-{branch}")
        git(self.tmp, "clone", "--quiet", up, clone)
        git(up, "commit", "--allow-empty", "-m", "second")
        return up, clone

    def unset_origin_head(self, clone):
        # git >= 2.48 recreates a missing origin/HEAD on fetch unless told not to
        git(clone, "config", "remote.origin.followRemoteHEAD", "never")
        git(clone, "remote", "set-head", "origin", "-d")

    def test_main_default_remote_is_seen_as_behind(self):
        _, clone = self.make_pair("main")
        r = self.bm.check_updates(clone, force=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["behind"], 1)
        self.assertFalse(r["up_to_date"])
        self.assertEqual(r["latest"]["subject"], "second")

    def test_master_default_remote_still_works(self):
        _, clone = self.make_pair("master")
        r = self.bm.check_updates(clone, force=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["behind"], 1)

    def test_falls_back_to_origin_main_when_origin_head_is_unset(self):
        _, clone = self.make_pair("main")
        self.unset_origin_head(clone)
        r = self.bm.check_updates(clone, force=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["behind"], 1)

    def test_no_known_remote_branch_is_an_error_not_up_to_date(self):
        _, clone = self.make_pair("trunk")
        self.unset_origin_head(clone)
        r = self.bm.check_updates(clone, force=True)
        self.assertFalse(r["ok"], r)
        self.assertIn("origin", r["error"])
        self.assertNotIn("behind", r)

    def test_explicit_missing_branch_reports_error_not_zero_behind(self):
        _, clone = self.make_pair("main")
        r = self.bm.check_updates(clone, remote_branch="origin/nope", force=True)
        self.assertFalse(r["ok"], r)
        self.assertNotIn("behind", r)


if __name__ == "__main__":
    unittest.main()
