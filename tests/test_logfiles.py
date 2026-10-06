import conftest_paths  # noqa: F401
import os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
import logfiles


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def read(self, name):
        with open(os.path.join(self.d, name), encoding="utf-8") as f:
            return f.read()

    def write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p


class TailLinesTest(_Tmp):
    """Issue #26: a 5.1 GB router.out.log was read whole on every poll."""

    def test_missing_file_is_empty(self):
        self.assertEqual(logfiles.tail_lines(os.path.join(self.d, "nope"), 5), [])

    def test_last_n_lines_keep_their_endings(self):
        p = self.write("a.log", "".join(f"line {i}\n" for i in range(100)))
        self.assertEqual(logfiles.tail_lines(p, 3), ["line 97\n", "line 98\n", "line 99\n"])

    def test_file_shorter_than_n(self):
        p = self.write("a.log", "one\ntwo\n")
        self.assertEqual(logfiles.tail_lines(p, 50), ["one\n", "two\n"])

    def test_no_trailing_newline(self):
        p = self.write("a.log", "one\ntwo\nthree")
        self.assertEqual(logfiles.tail_lines(p, 2), ["two\n", "three"])

    def test_crlf_lines(self):
        p = self.write("a.log", b"one\r\ntwo\r\nthree\r\n")
        self.assertEqual(logfiles.tail_lines(p, 2), ["two\n", "three\n"])

    def test_crlf_split_across_a_block_boundary(self):
        # The \r ends one block and the \n starts the next: still one line break.
        p = self.write("a.log", b"a" * 9 + b"\r\n" + b"b\r\n")
        self.assertEqual(logfiles.tail_lines(p, 5, block=10), ["a" * 9 + "\n", "b\n"])

    def test_zero_lines(self):
        p = self.write("a.log", "one\n")
        self.assertEqual(logfiles.tail_lines(p, 0), [])

    def test_bad_utf8_is_replaced(self):
        p = self.write("a.log", b"ok\n\xff\xfe bad\n")
        self.assertEqual(logfiles.tail_lines(p, 1), ["�� bad\n"])

    def test_large_file_reads_only_the_end(self):
        p = os.path.join(self.d, "big.log")
        with open(p, "wb") as f:
            chunk = b"x" * 1023 + b"\n"
            for _ in range(8 * 1024):        # 8 MiB of 1 KiB lines
                f.write(chunk)
            f.write(b"the end\n")
        read = []
        real_open = open

        def spy(*a, **kw):
            f = real_open(*a, **kw)
            orig = f.read
            f.read = lambda size=-1: (read.append(size), orig(size))[1]
            return f
        with mock.patch("builtins.open", spy):
            got = logfiles.tail_lines(p, 3)
        self.assertEqual(got[-1], "the end\n")
        self.assertEqual(len(got), 3)
        self.assertNotIn(-1, read)                 # never "read the rest"
        self.assertLess(sum(read), 256 * 1024)     # a block or two, not 8 MiB

    def test_byte_cap_bounds_one_giant_line(self):
        p = self.write("a.log", b"z" * (300 * 1024))   # no newline at all
        got = logfiles.tail_lines(p, 5, max_bytes=64 * 1024)
        self.assertEqual(len(got), 1)
        self.assertLessEqual(len(got[0]), 64 * 1024)


class RotateTest(_Tmp):
    def test_small_log_is_left_alone(self):
        p = self.write("router.out.log", "x" * 10)
        logfiles.rotate(p, max_bytes=100, keep=3)
        self.assertEqual(sorted(os.listdir(self.d)), ["router.out.log"])

    def test_missing_log_is_fine(self):
        logfiles.rotate(os.path.join(self.d, "nope.log"), max_bytes=1, keep=3)
        self.assertEqual(os.listdir(self.d), [])

    def test_big_log_moves_to_dot_1(self):
        p = self.write("router.out.log", "x" * 200)
        logfiles.rotate(p, max_bytes=100, keep=3)
        self.assertFalse(os.path.exists(p))
        self.assertEqual(os.path.getsize(p + ".1"), 200)

    def test_shifts_and_drops_the_oldest(self):
        p = self.write("r.log", "new" * 100)
        for i in (1, 2, 3):
            self.write(f"r.log.{i}", f"gen{i}")
        logfiles.rotate(p, max_bytes=100, keep=3)
        names = sorted(os.listdir(self.d))
        self.assertEqual(names, ["r.log.1", "r.log.2", "r.log.3"])
        self.assertEqual(self.read("r.log.1"), "new" * 100)
        self.assertEqual(self.read("r.log.2"), "gen1")
        self.assertEqual(self.read("r.log.3"), "gen2")      # gen3 was the oldest

    def test_a_locked_file_does_not_raise(self):
        p = self.write("r.log", "x" * 200)
        with mock.patch("os.replace", side_effect=PermissionError("in use")):
            logfiles.rotate(p, max_bytes=100, keep=3)   # appends to the old one
        self.assertTrue(os.path.exists(p))

    def test_open_append_rotates_first(self):
        p = self.write("r.log", "x" * 200)
        with logfiles.open_append(p, max_bytes=100, keep=2) as f:
            f.write("fresh\n")
        self.assertEqual(self.read("r.log"), "fresh\n")
        self.assertEqual(os.path.getsize(p + ".1"), 200)


# A child that writes "a" lines, waits for a line on stdin, then writes "b"
# lines - so the test can trim its log while the child still holds it open.
_CHILD = r"""
import sys
for i in range(50):
    print("a" * 99); sys.stdout.flush()
sys.stdin.readline()
for i in range(3):
    print("b" * 99); sys.stdout.flush()
"""


class TrimTest(_Tmp):
    """Rotation during a run: copy the log to .1, then truncate it in place."""

    def _child(self, f):
        p = subprocess.Popen([sys.executable, "-c", _CHILD], stdout=f,
                             stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        f.close()                        # the child holds its own handle
        self.addCleanup(lambda: (p.kill(), p.wait()) if p.poll() is None else None)
        return p

    def _wait_for(self, path, size):
        import time
        end = time.time() + 15
        while os.path.getsize(path) < size and time.time() < end:
            time.sleep(0.02)

    def test_small_log_is_left_alone(self):
        p = self.write("r.log", "x" * 10)
        self.assertFalse(logfiles.trim(p, max_bytes=100, keep=3))
        self.assertEqual(sorted(os.listdir(self.d)), ["r.log"])

    def test_missing_log_is_fine(self):
        self.assertFalse(logfiles.trim(os.path.join(self.d, "nope"), max_bytes=1))

    def test_trims_a_log_a_running_process_appends_to(self):
        p = os.path.join(self.d, "router.out.log")
        child = self._child(logfiles.open_append(p))
        self._wait_for(p, 50 * 100)                   # all 50 "a" lines are in
        self.assertTrue(logfiles.trim(p, max_bytes=1000, keep=3))
        child.communicate(b"go\n", timeout=15)
        with open(p, "rb") as f:
            now = f.read()
        with open(p + ".1", "rb") as f:
            old = f.read()
        self.assertNotIn(b"\0", now)          # the child wrote at the new end
        self.assertEqual(now.split(), [b"b" * 99] * 3)
        self.assertEqual(old.split(), [b"a" * 99] * 50)

    def test_shifts_older_copies(self):
        p = self.write("r.log", "new" * 100)
        self.write("r.log.1", "gen1")
        self.assertTrue(logfiles.trim(p, max_bytes=100, keep=3))
        self.assertEqual(self.read("r.log"), "")
        self.assertEqual(self.read("r.log.1"), "new" * 100)
        self.assertEqual(self.read("r.log.2"), "gen1")

    @unittest.skipUnless(os.name == "nt", "Windows handle semantics")
    def test_leaves_a_log_held_by_a_non_append_handle(self):
        # A router run.ps1 or an older panel started: truncating under it
        # would make it write past the new end and pad the file with NULs.
        p = os.path.join(self.d, "router.out.log")
        child = self._child(open(p, "a", encoding="utf-8"))
        self._wait_for(p, 50 * 100)
        self.assertFalse(logfiles.trim(p, max_bytes=1000, keep=3))
        child.communicate(b"go\n", timeout=15)
        self.assertFalse(os.path.exists(p + ".1"))
        with open(p, "rb") as f:
            self.assertEqual(len(f.read().split()), 53)

    def test_a_failed_copy_does_not_raise(self):
        p = self.write("r.log", "x" * 200)
        with mock.patch("shutil.copyfile", side_effect=OSError("disk full")):
            self.assertFalse(logfiles.trim(p, max_bytes=100, keep=3))
        self.assertEqual(os.path.getsize(p), 200)     # nothing lost

    def test_trim_all_covers_router_vllm_and_model_logs(self):
        big = "x" * 200
        for n in ("router.out.log", "router.err.log", "vllm.out.log",
                  "vllm.err.log", "slot-m-abc123.log", "build.log", "panel.pid"):
            self.write(n, big)
        logfiles.trim_all(self.d, max_bytes=100)
        rotated = sorted(n[:-2] for n in os.listdir(self.d) if n.endswith(".1"))
        self.assertEqual(rotated, ["router.err.log", "router.out.log",
                                   "slot-m-abc123.log", "vllm.err.log", "vllm.out.log"])


class TrimmerTest(unittest.TestCase):
    def test_runs_until_stopped(self):
        import threading
        calls = threading.Semaphore(0)
        with mock.patch.object(logfiles, "trim_all", side_effect=lambda d: calls.release()):
            stop = logfiles.start_trimmer("logs", every=0.01)
            self.assertTrue(calls.acquire(timeout=5))
            self.assertTrue(calls.acquire(timeout=5))     # it repeats
            stop.set()
        t = [t for t in threading.enumerate() if t.name == "log-trimmer"]
        for th in t:
            th.join(timeout=5)
            self.assertFalse(th.is_alive())


if __name__ == "__main__":
    unittest.main()
