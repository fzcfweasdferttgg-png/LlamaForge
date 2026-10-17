import conftest_paths  # noqa: F401
import unittest

import routes


class TestBenchList(unittest.TestCase):
    """Bad bench parameters are answers (400 naming the parameter), not500s."""

    def test_a_comma_string_is_a_list_of_sizes(self):
        self.assertEqual(routes._bench_list("512,1024", "b"), [512, 1024])

    def test_a_json_list_is_accepted(self):
        self.assertEqual(routes._bench_list([512, "1024"], "ub"), [512, 1024])

    def test_a_bare_number_names_its_parameter(self):
        with self.assertRaises(ValueError) as cm:
            routes._bench_list(512, "b")
        self.assertIn("b", str(cm.exception))

    def test_a_typo_names_its_parameter(self):
        with self.assertRaises(ValueError) as cm:
            routes._bench_list("512,x", "ub")
        self.assertIn("ub", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
