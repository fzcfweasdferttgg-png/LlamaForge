import conftest_paths  # noqa: F401
import unittest
from unittest import mock

import routes
import stats


class TestStatsSeesProcessSlots(unittest.TestCase):
    def test_the_poller_is_wired_to_the_slot_processes(self):
        self.assertIs(stats.TRACKER.proc_source, routes._proc_endpoints)

    def test_only_ready_slots_are_scraped(self):
        status = {
            "ik-moe": {"state": "ready", "endpoint": "http://127.0.0.1:8100"},
            "old-gemma": {"state": "loading", "endpoint": "http://127.0.0.1:8101"},
            "crashed": {"state": "failed", "endpoint": "http://127.0.0.1:8102"},
        }
        with mock.patch.object(routes.PROCS, "status", return_value=status):
            self.assertEqual(routes._proc_endpoints(), {"ik-moe": "http://127.0.0.1:8100"})


if __name__ == "__main__":
    unittest.main()
