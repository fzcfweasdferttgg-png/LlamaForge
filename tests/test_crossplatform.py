import conftest_paths  # noqa: F401
import unittest
import scanner


class TestPosixRoots(unittest.TestCase):
    def test_home_plus_existing_mounts(self):
        exists = {"/mnt", "/data"}
        roots = scanner.posix_roots("/home/u", ["/mnt", "/media", "/srv", "/data"],
                                    isdir=lambda p: p in exists)
        self.assertEqual(roots, ["/home/u", "/mnt", "/data"])

    def test_home_only_when_no_mounts(self):
        roots = scanner.posix_roots("/Users/u", ["/Volumes"], isdir=lambda p: False)
        self.assertEqual(roots, ["/Users/u"])


if __name__ == "__main__":
    unittest.main()
