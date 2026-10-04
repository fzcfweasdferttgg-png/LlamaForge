import conftest_paths  # noqa: F401
import datetime as dt, json, os, shutil, subprocess, sys, tempfile, time, unittest

from embers import lock


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class LockCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, lock.LOCK_NAME)

    def write(self, data, age=None):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        if age is not None:
            t = time.time() - age
            os.utime(self.path, (t, t))

    def since(self, hours_ago=0):
        return (dt.datetime.now() - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")


class HeldTest(LockCase):
    def test_acquire_creates_and_release_removes(self):
        with lock.held(self.root):
            self.assertTrue(os.path.isfile(self.path))
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["pid"], os.getpid())
            self.assertIsInstance(data["since"], str)
        self.assertFalse(os.path.exists(self.path))

    def test_same_process_second_hold_is_busy(self):
        with lock.held(self.root):
            with self.assertRaises(lock.Busy) as cm:
                with lock.held(os.path.join(self.root, ".")):
                    pass
            self.assertEqual(cm.exception.pid, os.getpid())
            self.assertTrue(os.path.isfile(self.path))     # the outer hold is untouched
        self.assertFalse(os.path.exists(self.path))
        with lock.held(self.root):                          # released for reuse
            pass

    def test_release_on_exception(self):
        with self.assertRaises(ValueError):
            with lock.held(self.root):
                raise ValueError("boom")
        self.assertFalse(os.path.exists(self.path))
        with lock.held(self.root):
            pass

    def test_dead_pid_is_broken(self):
        self.write({"pid": _dead_pid(), "since": self.since()})
        with lock.held(self.root):
            with open(self.path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["pid"], os.getpid())

    def test_live_foreign_pid_blocks(self):
        self.write({"pid": os.getppid(), "since": self.since()})
        with self.assertRaises(lock.Busy) as cm:
            with lock.held(self.root):
                pass
        self.assertEqual(cm.exception.pid, os.getppid())
        self.assertTrue(os.path.isfile(self.path))          # never removed someone else's lock

    def test_old_garbage_is_broken_fresh_garbage_is_busy(self):
        self.write("{not json", age=0)
        with self.assertRaises(lock.Busy) as cm:
            with lock.held(self.root):
                pass
        self.assertIsNone(cm.exception.pid)
        self.write("{not json", age=lock.GARBAGE_AGE + 30)
        with lock.held(self.root):
            pass
        self.assertFalse(os.path.exists(self.path))

    def test_too_old_since_is_broken(self):
        self.write({"pid": os.getppid(), "since": self.since(hours_ago=13)})
        with lock.held(self.root):
            with open(self.path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["pid"], os.getpid())

    def test_lock_taken_over_is_not_removed_on_exit(self):
        with lock.held(self.root):
            self.write({"pid": os.getppid(), "since": self.since()})
        self.assertTrue(os.path.isfile(self.path))


class PidAliveTest(unittest.TestCase):
    def test_pid_alive(self):
        self.assertTrue(lock.pid_alive(os.getpid()))
        self.assertFalse(lock.pid_alive(_dead_pid()))
        for bad in (0, -1, True, "12", None, 3.0):
            self.assertFalse(lock.pid_alive(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
