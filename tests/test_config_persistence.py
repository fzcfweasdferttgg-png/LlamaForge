"""config.json / models.ini durability: atomic writes, locking, and the
corrupt-file path that used to silently erase a user's settings."""
import conftest_paths  # noqa: F401
import json, os, tempfile, threading, unittest

import atomicio, config


class AtomicIOTest(unittest.TestCase):
    """Atomicity must not depend on the caller holding a lock: a second
    LlamaForge instance writing the same file is a different process."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "doc.json")

    def test_concurrent_writers_never_collide_or_tear(self):
        errors = []

        def write(i):
            try:
                atomicio.write_json(self.path, {"writer": i, "pad": "x" * 5000})
            except Exception as e:      # a shared .tmp name fails here on Windows
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent atomic writes collided: {errors[:3]}")
        with open(self.path, encoding="utf-8") as f:
            self.assertIsInstance(json.load(f)["writer"], int)   # never a torn file

    def test_no_scratch_files_are_left_behind(self):
        atomicio.write_text(self.path, "hello")
        self.assertEqual(os.listdir(self.tmp), ["doc.json"])

    def test_failed_write_cleans_up_and_leaves_original(self):
        atomicio.write_text(self.path, "original")

        class Boom:
            def __str__(self):          # json.dumps raises partway through
                raise RuntimeError("boom")

        with self.assertRaises(Exception):
            atomicio.write_text(self.path, Boom())
        self.assertEqual(os.listdir(self.tmp), ["doc.json"])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "original")


class CorruptConfigTest(unittest.TestCase):
    """A config.json that won't parse must not look like a fresh install."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.tmp, "config.json")
        config.LOAD_ERROR = None

    def tearDown(self):
        config.CONFIG = self._orig
        config.LOAD_ERROR = None

    def test_corrupt_config_is_quarantined_not_erased(self):
        with open(config.CONFIG, "w", encoding="utf-8") as f:
            f.write('{"router_port": 9999, "presets": {"mine": ')   # truncated
        cfg = config.load()
        # falls back to defaults so the dashboard still starts...
        self.assertEqual(cfg["router_port"], config.DEFAULTS["router_port"])
        # ...but says so, instead of pretending nothing happened
        self.assertIsNotNone(config.LOAD_ERROR)
        self.assertIn("could not be read", config.LOAD_ERROR)
        # ...and the original bytes survive the next save()
        config.save(cfg)
        with open(config.CONFIG + ".corrupt", encoding="utf-8") as f:
            self.assertIn("9999", f.read())

    def test_quarantine_is_write_once(self):
        with open(config.CONFIG, "w", encoding="utf-8") as f:
            f.write("{truncated")
        config.load()
        with open(config.CONFIG, "w", encoding="utf-8") as f:
            f.write("{also broken but later")
        config.load()
        with open(config.CONFIG + ".corrupt", encoding="utf-8") as f:
            self.assertEqual(f.read(), "{truncated")   # the true original

    def test_good_config_clears_the_error(self):
        with open(config.CONFIG, "w", encoding="utf-8") as f:
            f.write("{broken")
        config.load()
        self.assertIsNotNone(config.LOAD_ERROR)
        config.save({"router_port": 1234})
        config.load()
        self.assertIsNone(config.LOAD_ERROR)


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.tmp, "config.json")

    def tearDown(self):
        config.CONFIG = self._orig

    def test_save_leaves_no_tmp_file_behind(self):
        config.save({"router_port": 8080})
        self.assertFalse(os.path.exists(config.CONFIG + ".tmp"))

    def test_ini_write_is_atomic(self):
        path = os.path.join(self.tmp, "models.ini")
        config.set_keys("alpha", {"model": "/m/a.gguf"}, path)
        self.assertFalse(os.path.exists(path + ".tmp"))
        self.assertEqual(config.read_sections(path)["alpha"]["model"], "/m/a.gguf")


class ConcurrentWriteTest(unittest.TestCase):
    """The bug this guards: load->mutate->save from several threads used to
    drop all but the last writer's change."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = config.CONFIG
        config.CONFIG = os.path.join(self.tmp, "config.json")
        config.save({})

    def tearDown(self):
        config.CONFIG = self._orig

    def test_concurrent_mutate_keeps_every_change(self):
        def add(i):
            config.mutate(lambda c: c.setdefault("presets", {}).__setitem__(
                f"p{i}", {"temp": str(i)}))

        threads = [threading.Thread(target=add, args=(i,)) for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(config.CONFIG, encoding="utf-8") as f:
            presets = json.load(f)["presets"]
        self.assertEqual(len(presets), 25, "a concurrent write was lost")

    def test_concurrent_update_keeps_every_key(self):
        threads = [threading.Thread(target=config.update, args=({f"k{i}": i},))
                   for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        saved = config.load()
        for i in range(25):
            self.assertEqual(saved[f"k{i}"], i)


class ConcurrentIniTest(unittest.TestCase):
    def test_concurrent_set_keys_keeps_every_section(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "models.ini")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[*]\nctx-size = 4096\n")

        threads = [threading.Thread(target=config.set_keys,
                                    args=(f"m{i}", {"model": f"/m/{i}.gguf"}, path))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        secs = config.read_sections(path)
        for i in range(20):
            self.assertIn(f"m{i}", secs, "a concurrent models.ini write was lost")
        self.assertEqual(secs["*"]["ctx-size"], "4096")   # untouched


class IniLayoutTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "models.ini")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_new_keys_follow_the_last_key_even_when_it_is_replaced(self):
        self.write("[m]\nmodel = /m.gguf\nmain-gpu = 1\n\n[n]\nmodel = /n.gguf\n")
        config.set_keys("m", {"main-gpu": "0", "device": "CUDA1"}, self.path)
        self.assertEqual(self.text(), "[m]\nmodel = /m.gguf\nmain-gpu = 0\ndevice = CUDA1\n\n"
                                      "[n]\nmodel = /n.gguf\n")

    def test_raw_read_keeps_inline_comments(self):
        self.write("[m]\nmain-gpu = 1 ; the 5080\nctx-size = 8192\n")
        self.assertEqual(config.read_sections(self.path)["m"]["main-gpu"], "1")
        self.assertEqual(config.read_sections(self.path, raw=True)["m"],
                         {"main-gpu": "1 ; the 5080", "ctx-size": "8192"})


if __name__ == "__main__":
    unittest.main()
