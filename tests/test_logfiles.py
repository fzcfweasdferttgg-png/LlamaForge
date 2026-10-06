import conftest_paths  # noqa: F401
import os, shutil, tempfile, unittest
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
        self.assertEqual(open(p, encoding="utf-8").read(), "fresh\n")
        self.assertEqual(os.path.getsize(p + ".1"), 200)


if __name__ == "__main__":
    unittest.main()
