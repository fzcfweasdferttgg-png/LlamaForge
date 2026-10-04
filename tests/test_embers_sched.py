import conftest_paths  # noqa: F401
import unittest
from datetime import datetime as dt

from embers import sched

# 2026-10-04 is a Sunday; 2026-10-05 a Monday.
SUN = dt(2026, 10, 4)
JOBS = {"ingest": "02:00", "brief": "07:00", "lint": "sun 03:00"}


class ParseWhenTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(sched.parse_when("07:00"), (None, 7, 0))
        self.assertEqual(sched.parse_when("23:59"), (None, 23, 59))
        self.assertEqual(sched.parse_when("sun 03:00"), (6, 3, 0))
        self.assertEqual(sched.parse_when("mon 00:30"), (0, 0, 30))

    def test_invalid(self):
        for bad in ("off", "", "25:00", "sun 3:00", "SUN 03:00", "07:00 ", None, 7):
            self.assertIsNone(sched.parse_when(bad), bad)


class OccurrenceTest(unittest.TestCase):
    def test_daily_exactly_at_slot(self):
        now = dt(2026, 10, 4, 7, 0)
        self.assertEqual(sched.last_occurrence("07:00", now), now)
        self.assertEqual(sched.next_occurrence("07:00", now), dt(2026, 10, 5, 7, 0))

    def test_daily_before_and_after(self):
        now = dt(2026, 10, 4, 6, 59, 59)
        self.assertEqual(sched.last_occurrence("07:00", now), dt(2026, 10, 3, 7, 0))
        self.assertEqual(sched.next_occurrence("07:00", now), dt(2026, 10, 4, 7, 0))
        now = dt(2026, 10, 4, 12, 0)
        self.assertEqual(sched.last_occurrence("07:00", now), dt(2026, 10, 4, 7, 0))
        self.assertEqual(sched.next_occurrence("07:00", now), dt(2026, 10, 5, 7, 0))

    def test_midnight_boundary(self):
        before = dt(2026, 10, 4, 23, 59)
        after = dt(2026, 10, 5, 0, 1)
        self.assertEqual(sched.last_occurrence("00:00", before), dt(2026, 10, 4, 0, 0))
        self.assertEqual(sched.next_occurrence("00:00", before), dt(2026, 10, 5, 0, 0))
        self.assertEqual(sched.last_occurrence("00:00", after), dt(2026, 10, 5, 0, 0))
        self.assertEqual(sched.next_occurrence("00:00", after), dt(2026, 10, 6, 0, 0))
        self.assertEqual(sched.last_occurrence("23:30", after), dt(2026, 10, 4, 23, 30))
        self.assertEqual(sched.next_occurrence("23:30", after), dt(2026, 10, 5, 23, 30))

    def test_weekly_sunday_before_and_after_slot(self):
        before = dt(2026, 10, 4, 2, 0)
        self.assertEqual(sched.last_occurrence("sun 03:00", before), dt(2026, 9, 27, 3, 0))
        self.assertEqual(sched.next_occurrence("sun 03:00", before), dt(2026, 10, 4, 3, 0))
        after = dt(2026, 10, 4, 4, 0)
        self.assertEqual(sched.last_occurrence("sun 03:00", after), dt(2026, 10, 4, 3, 0))
        self.assertEqual(sched.next_occurrence("sun 03:00", after), dt(2026, 10, 11, 3, 0))

    def test_weekly_exactly_at_slot(self):
        now = dt(2026, 10, 4, 3, 0)
        self.assertEqual(sched.last_occurrence("sun 03:00", now), now)
        self.assertEqual(sched.next_occurrence("sun 03:00", now), dt(2026, 10, 11, 3, 0))

    def test_weekly_across_week_boundary(self):
        # Monday morning: last Sunday slot was yesterday, next is in six days.
        mon = dt(2026, 10, 5, 1, 0)
        self.assertEqual(sched.last_occurrence("sun 03:00", mon), dt(2026, 10, 4, 3, 0))
        self.assertEqual(sched.next_occurrence("sun 03:00", mon), dt(2026, 10, 11, 3, 0))
        # A Monday slot queried on Sunday: last was six days ago, next is tomorrow.
        self.assertEqual(sched.last_occurrence("mon 09:00", SUN.replace(hour=12)),
                         dt(2026, 9, 28, 9, 0))
        self.assertEqual(sched.next_occurrence("mon 09:00", SUN.replace(hour=12)),
                         dt(2026, 10, 5, 9, 0))
        # Across a month/year boundary.
        self.assertEqual(sched.next_occurrence("fri 08:00", dt(2026, 12, 31, 9, 0)),
                         dt(2027, 1, 1, 8, 0))

    def test_off_is_none(self):
        for spec in ("off", "", None, "SUN 03:00"):
            self.assertIsNone(sched.last_occurrence(spec, SUN))
            self.assertIsNone(sched.next_occurrence(spec, SUN))


class DueJobsTest(unittest.TestCase):
    NONE = {"ingest": None, "brief": None, "lint": None}

    def test_created_after_todays_slot_not_due(self):
        now = dt(2026, 10, 4, 9, 0)
        created = dt(2026, 10, 4, 8, 0)
        self.assertEqual(sched.due_jobs({"brief": "07:00"}, {}, created, now), [])

    def test_created_yesterday_never_run_is_due(self):
        now = dt(2026, 10, 4, 9, 0)
        created = dt(2026, 10, 3, 12, 0)
        self.assertEqual(sched.due_jobs({"brief": "07:00"}, {"brief": None}, created, now),
                         ["brief"])

    def test_missed_for_days_runs_once(self):
        created = dt(2026, 9, 1)
        now = dt(2026, 10, 4, 9, 0)
        last = {"brief": dt(2026, 9, 28, 7, 0)}
        self.assertEqual(sched.due_jobs({"brief": "07:00"}, last, created, now), ["brief"])
        # An attempt at the slot or after it settles it.
        for at in (dt(2026, 10, 4, 7, 0), dt(2026, 10, 4, 8, 30)):
            self.assertEqual(sched.due_jobs({"brief": "07:00"}, {"brief": at}, created, now), [])

    def test_attempt_before_latest_slot_is_due(self):
        created = dt(2026, 9, 1)
        now = dt(2026, 10, 4, 9, 0)
        last = {"brief": dt(2026, 10, 4, 6, 59)}
        self.assertEqual(sched.due_jobs({"brief": "07:00"}, last, created, now), ["brief"])

    def test_order_regardless_of_dict_order(self):
        conf = {"lint": "sun 03:00", "brief": "07:00", "ingest": "02:00"}
        created = dt(2026, 9, 1)
        now = dt(2026, 10, 4, 9, 0)
        self.assertEqual(sched.due_jobs(conf, self.NONE, created, now),
                         ["ingest", "brief", "lint"])

    def test_off_never_due(self):
        created = dt(2020, 1, 1)
        conf = {"ingest": "off", "brief": "07:00", "lint": None}
        self.assertEqual(sched.due_jobs(conf, {}, created, dt(2026, 10, 4, 9, 0)), ["brief"])

    def test_unknown_keys_and_non_str_specs_ignored(self):
        created = dt(2020, 1, 1)
        conf = {"reindex": "02:00", "brief": 700, "ingest": "02:00"}
        self.assertEqual(sched.due_jobs(conf, {}, created, dt(2026, 10, 4, 9, 0)), ["ingest"])

    def test_not_due_before_first_slot_of_the_week(self):
        created = dt(2026, 10, 1)  # Thursday
        now = dt(2026, 10, 4, 2, 0)  # Sunday, before 03:00
        self.assertEqual(sched.due_jobs({"lint": "sun 03:00"}, {}, created, now), [])
        self.assertEqual(sched.due_jobs({"lint": "sun 03:00"}, {}, created,
                                        now.replace(hour=3)), ["lint"])


class DecideTest(unittest.TestCase):
    def d(self, want="", loaded="m", busy=0, idle_for=0, waited=0, swap_allowed=True):
        return sched.decide(want, loaded, busy, idle_for, waited, swap_allowed)

    def test_router_unreachable(self):
        self.assertEqual(self.d(busy=None), ("wait", "the router is not reachable"))

    def test_router_busy(self):
        self.assertEqual(self.d(busy=2), ("wait", "the router is busy"))

    def test_any_model_uses_loaded(self):
        self.assertEqual(self.d(want="", loaded="qwen", busy=0), ("run", "qwen"))

    def test_any_model_none_loaded(self):
        self.assertEqual(self.d(want="", loaded=None), ("wait", "no model is loaded"))
        self.assertEqual(self.d(want="", loaded=""), ("wait", "no model is loaded"))

    def test_non_str_want_treated_as_any(self):
        self.assertEqual(self.d(want=None, loaded="qwen"), ("run", "qwen"))
        self.assertEqual(self.d(want=5, loaded=None), ("wait", "no model is loaded"))

    def test_pinned_model_loaded(self):
        self.assertEqual(self.d(want="qwen", loaded="qwen"), ("run", "qwen"))

    def test_pinned_swap_off(self):
        self.assertEqual(self.d(want="qwen", loaded="gemma", swap_allowed=False),
                         ("wait", "needs qwen; model swapping is off"))
        self.assertEqual(self.d(want="qwen", loaded=None, swap_allowed=False,
                                idle_for=99999),
                         ("wait", "needs qwen; model swapping is off"))

    def test_pinned_swap_after_idle(self):
        self.assertEqual(self.d(want="qwen", loaded="gemma", idle_for=sched.SWAP_IDLE),
                         ("swap", "qwen"))
        self.assertEqual(self.d(want="qwen", loaded=None, idle_for=sched.SWAP_IDLE + 1),
                         ("swap", "qwen"))

    def test_pinned_waits_for_idle(self):
        self.assertEqual(self.d(want="qwen", loaded="gemma", idle_for=sched.SWAP_IDLE - 1),
                         ("wait", "waiting for 10 idle minutes before loading qwen"))

    def test_busy_beats_everything(self):
        self.assertEqual(self.d(want="qwen", loaded="qwen", busy=1),
                         ("wait", "the router is busy"))

    def test_wait_max_converts_each_wait_to_skip(self):
        cases = [
            (dict(busy=None), "the router is not reachable"),
            (dict(busy=3), "the router is busy"),
            (dict(want="", loaded=None), "no model is loaded"),
            (dict(want="qwen", loaded="gemma", swap_allowed=False),
             "needs qwen; model swapping is off"),
            (dict(want="qwen", loaded="gemma", idle_for=10),
             "waiting for 10 idle minutes before loading qwen"),
        ]
        for kw, reason in cases:
            self.assertEqual(self.d(waited=sched.WAIT_MAX - 1, **kw), ("wait", reason))
            self.assertEqual(self.d(waited=sched.WAIT_MAX, **kw),
                             ("skip", f"{reason} for 60 minutes"))

    def test_run_and_swap_not_affected_by_waited(self):
        self.assertEqual(self.d(want="", loaded="m", waited=99999), ("run", "m"))
        self.assertEqual(self.d(want="q", loaded="m", idle_for=sched.SWAP_IDLE,
                                waited=99999), ("swap", "q"))


class ConstantsTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(sched.WAIT_MAX, 3600)
        self.assertEqual(sched.SWAP_IDLE, 600)
        self.assertEqual(sched.ORDER, ("ingest", "brief", "lint"))
        self.assertEqual(sched.DAYS[6], "sun")


if __name__ == "__main__":
    unittest.main()
