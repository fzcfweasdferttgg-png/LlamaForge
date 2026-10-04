import conftest_paths  # noqa: F401
import errno, json, os, shutil, sys, tempfile, time, unittest
from unittest import mock

from embers import lock
from embers_testkit import LockHolder


class LockCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, lock.LOCK_NAME)

    def holder(self):
        h = LockHolder(self.root)
        self.addCleanup(h.kill)
        return h

    def read_info(self):
        with open(self.path, "rb") as f:
            return json.loads(f.read(lock.MAX_READ).decode("utf-8"))

    def acquire_eventually(self, seconds=10):
        """The kernel drops a dead process's lock promptly but not necessarily instantly."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                with lock.held(self.root):
                    return True
            except lock.Busy:
                if time.monotonic() > deadline:
                    return False
                time.sleep(0.05)


class HeldTest(LockCase):
    def test_acquire_writes_diagnostics_and_file_stays_after_release(self):
        with lock.held(self.root):
            info = self.read_info()
            self.assertEqual(info["pid"], os.getpid())
            self.assertIsInstance(info["since"], str)
        self.assertTrue(os.path.isfile(self.path))       # permanent, never deleted
        with lock.held(self.root):                        # and reusable
            pass

    def test_same_process_second_hold_is_busy(self):
        spellings = [self.root, self.root + os.sep, os.path.join(self.root, ".")]
        if sys.platform == "win32":
            spellings += [self.root.upper(), self.root.lower()]
        with lock.held(self.root):
            for other in spellings:
                with self.assertRaises(lock.Busy, msg=other) as cm:
                    with lock.held(other):
                        pass
                self.assertEqual(cm.exception.pid, os.getpid())
            self.assertEqual(self.read_info()["pid"], os.getpid())   # the outer hold is untouched
        with lock.held(self.root + os.sep):               # released for reuse
            pass

    def test_release_on_exception(self):
        with self.assertRaises(ValueError):
            with lock.held(self.root):
                raise ValueError("boom")
        with lock.held(self.root):
            pass

    def test_release_never_raises(self):
        # Closing the fd behind the lock's back makes unlock and close fail; the body's
        # result must still come through and the in-process guard must be cleared.
        real_open, fds = os.open, []

        def spy(*a, **k):
            fd = real_open(*a, **k)
            fds.append(fd)
            return fd
        with mock.patch.object(lock.os, "open", spy):
            with lock.held(self.root):
                os.close(fds[0])
        with lock.held(self.root):
            pass

    def _patch_lock_call(self, side_effect):
        if sys.platform == "win32":
            return mock.patch.object(lock.msvcrt, "locking", side_effect=side_effect)
        return mock.patch.object(lock.fcntl, "flock", side_effect=side_effect)

    def test_lock_unsupported_raises_instead_of_busy(self):
        # ENOLCK (e.g. a filesystem without locks) is not "someone else has it":
        # reporting Busy would say "already running" forever.
        with self._patch_lock_call(OSError(errno.ENOLCK, "no locks available")):
            with self.assertRaises(OSError) as cm:
                with lock.held(self.root):
                    pass
        self.assertNotIsInstance(cm.exception, lock.Busy)
        with lock.held(self.root):                       # guard cleared, fd not leaked
            pass

    def test_interrupt_during_acquire_closes_the_fd(self):
        real_open, fds = os.open, []

        def spy(*a, **k):
            fd = real_open(*a, **k)
            fds.append(fd)
            return fd
        with mock.patch.object(lock.os, "open", spy), \
                mock.patch.object(lock, "_write_info", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                with lock.held(self.root):
                    pass
        with self.assertRaises(OSError):                 # already closed
            os.fstat(fds[0])
        h = self.holder()                                # and another process can take it
        h.release()


class CrossProcessTest(LockCase):
    def test_other_process_blocks_until_it_releases(self):
        h = self.holder()
        info = self.read_info()                           # readable while locked
        self.assertEqual(info["pid"], h.pid)
        with self.assertRaises(lock.Busy) as cm:
            with lock.held(self.root):
                pass
        self.assertEqual(cm.exception.pid, h.pid)
        self.assertTrue(cm.exception.since)
        self.assertEqual(cm.exception.path, self.path)
        h.release()
        with lock.held(self.root):
            self.assertEqual(self.read_info()["pid"], os.getpid())

    def test_killed_holder_releases_the_lock(self):
        h = self.holder()
        with self.assertRaises(lock.Busy):
            with lock.held(self.root):
                pass
        h.kill()
        self.assertTrue(self.acquire_eventually())

    def test_unreadable_diagnostics_give_unknown_pid(self):
        self.holder()
        with open(self.path, "wb") as f:                  # the JSON region is not locked
            f.write(b"{not json")
        with self.assertRaises(lock.Busy) as cm:
            with lock.held(self.root):
                pass
        self.assertIsNone(cm.exception.pid)


if __name__ == "__main__":
    unittest.main()
