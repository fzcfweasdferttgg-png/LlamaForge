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


class DecidePoolTest(unittest.TestCase):
    """A multi-model pool: several models up at once, the main first. An ember
    runs on any of them, loads its own beside them when the planner says it
    fits, and never evicts anything."""

    def d(self, want="", loaded=("main",), busy=None, waited=0, swap_allowed=True, plan=None):
        busy = {m: 0 for m in loaded} if busy is None else busy
        return sched.decide_pool(want, list(loaded), busy, waited, swap_allowed, plan)

    def test_router_unreachable(self):
        self.assertEqual(sched.decide_pool("", ["main"], None, 0, True),
                         ("wait", "the router is not reachable"))

    def test_any_model_runs_on_the_main(self):
        self.assertEqual(self.d(loaded=("main", "small")), ("run", "main"))

    def test_any_model_waits_while_the_main_is_busy(self):
        self.assertEqual(self.d(loaded=("main", "small"), busy={"main": 1, "small": 0}),
                         ("wait", "main is busy"))

    def test_nothing_loaded(self):
        self.assertEqual(self.d(loaded=(), busy={}), ("wait", "no model is loaded"))

    def test_a_loaded_worker_runs_while_the_main_works(self):
        self.assertEqual(self.d(want="small", loaded=("main", "small"),
                                busy={"main": 2, "small": 0}), ("run", "small"))

    def test_a_busy_model_waits(self):
        self.assertEqual(self.d(want="small", loaded=("main", "small"),
                                busy={"main": 0, "small": 1}), ("wait", "small is busy"))

    def test_loads_beside_when_it_fits(self):
        self.assertEqual(self.d(want="q", plan={"ok": True}), ("coload", "q"))

    def test_loads_into_an_empty_pool(self):
        self.assertEqual(self.d(want="q", loaded=(), busy={}, plan={"ok": True}), ("coload", "q"))

    def test_waits_with_the_planners_reason(self):
        self.assertEqual(self.d(want="q", plan={"ok": False, "reason": "needs ~9.0 GiB"}),
                         ("wait", "q can't load beside the loaded models: needs ~9.0 GiB"))

    def test_never_loads_while_anything_is_busy(self):
        self.assertEqual(self.d(want="q", busy={"main": 1}, plan={"ok": True}),
                         ("wait", "the router is busy"))

    def test_swapping_off_means_no_loads_either(self):
        self.assertEqual(self.d(want="q", swap_allowed=False, plan={"ok": True}),
                         ("wait", "needs q; model swapping is off"))

    def test_no_plan_is_a_wait(self):
        self.assertEqual(self.d(want="q", plan=None)[0], "wait")

    def test_wait_max_skips(self):
        self.assertEqual(self.d(want="q", plan={"ok": False, "reason": "full"},
                                waited=sched.WAIT_MAX),
                         ("skip", "q can't load beside the loaded models: full for 60 minutes"))

    def test_junk_is_tolerated(self):
        self.assertEqual(sched.decide_pool(None, ["m", 5, ""], {"m": 0}, 0, True), ("run", "m"))
        self.assertEqual(self.d(loaded=("m",), busy={"m": "1"}), ("wait", "the router is not reachable"))
        self.assertEqual(sched.decide_pool("", "m", {"m": 0}, 0, True), ("wait", "no model is loaded"))


class EdgeDatesTest(unittest.TestCase):
    def test_daily_across_year_boundary(self):
        self.assertEqual(sched.last_occurrence("23:00", dt(2027, 1, 1, 0, 30)),
                         dt(2026, 12, 31, 23, 0))

    def test_seconds_and_microseconds_at_slot_minute(self):
        now = dt(2026, 10, 4, 7, 0, 42, 123456)
        self.assertEqual(sched.last_occurrence("07:00", now), dt(2026, 10, 4, 7, 0))
        self.assertEqual(sched.next_occurrence("07:00", now), dt(2026, 10, 5, 7, 0))

    def test_created_exactly_at_slot_not_due(self):
        occ = dt(2026, 10, 4, 7, 0)
        self.assertEqual(sched.due_jobs({"brief": "07:00"}, {}, occ, occ.replace(hour=9)), [])


class DueJobsJunkTest(unittest.TestCase):
    NOW = dt(2026, 10, 4, 9, 0)
    OLD = dt(2020, 1, 1)

    def test_non_dict_jobs_conf(self):
        for bad in (None, "02:00", ["ingest"], 5):
            self.assertEqual(sched.due_jobs(bad, {}, self.OLD, self.NOW), [], bad)

    def test_non_dict_last_attempts(self):
        for bad in (None, "x", [None], 5):
            self.assertEqual(sched.due_jobs({"brief": "07:00"}, bad, self.OLD, self.NOW), [], bad)

    def test_non_datetime_created(self):
        for bad in (None, "2020-01-01T00:00:00", 0, self.OLD.date()):
            self.assertEqual(sched.due_jobs({"brief": "07:00"}, {}, bad, self.NOW), [], bad)

    def test_non_datetime_last_attempt_counts_as_never(self):
        for bad in ("2026-10-04T08:00:00", 12345, True, {}):
            self.assertEqual(sched.due_jobs({"brief": "07:00"}, {"brief": bad},
                                            self.OLD, self.NOW), ["brief"], bad)


class DecideJunkTest(unittest.TestCase):
    def d(self, want="", loaded="m", busy=0, idle_for=0, waited=0, swap_allowed=True):
        return sched.decide(want, loaded, busy, idle_for, waited, swap_allowed)

    def test_bad_busy_is_unreachable(self):
        for bad in (True, False, "0", "1", float("nan"), float("inf"), -1, -0.5, [], {}):
            self.assertEqual(self.d(busy=bad), ("wait", "the router is not reachable"), bad)

    def test_float_busy_ok(self):
        self.assertEqual(self.d(busy=0.0), ("run", "m"))
        self.assertEqual(self.d(busy=1.0), ("wait", "the router is busy"))

    def test_swap_allowed_must_be_true(self):
        for bad in ("no", "false", "yes", 1, [1], None):
            self.assertEqual(self.d(want="q", loaded="m", idle_for=sched.SWAP_IDLE,
                                    swap_allowed=bad),
                             ("wait", "needs q; model swapping is off"), bad)

    def test_bad_waited_counts_as_wait_max(self):
        for bad in (None, float("nan"), "0", True, []):
            self.assertEqual(self.d(busy=None, waited=bad),
                             ("skip", "the router is not reachable for 60 minutes"), bad)

    def test_bad_idle_for_never_swaps(self):
        wait = ("wait", "waiting for 10 idle minutes before loading q")
        for bad in (None, float("nan"), "9999", True, float("inf")):
            self.assertEqual(self.d(want="q", loaded="m", idle_for=bad), wait, bad)

    def test_bad_loaded_is_none(self):
        for bad in (5, "", True, ["m"], {"id": "m"}):
            self.assertEqual(self.d(want="", loaded=bad), ("wait", "no model is loaded"), bad)
        # A junk loaded never matches a pinned model.
        self.assertEqual(self.d(want="q", loaded=5, idle_for=sched.SWAP_IDLE), ("swap", "q"))

    def test_text_derived_from_constants(self):
        old = sched.SWAP_IDLE, sched.WAIT_MAX
        try:
            sched.SWAP_IDLE, sched.WAIT_MAX = 300, 1800
            self.assertEqual(self.d(want="q", loaded="m", idle_for=0),
                             ("wait", "waiting for 5 idle minutes before loading q"))
            self.assertEqual(self.d(busy=None, waited=1800),
                             ("skip", "the router is not reachable for 30 minutes"))
        finally:
            sched.SWAP_IDLE, sched.WAIT_MAX = old


class ConstantsTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(sched.WAIT_MAX, 3600)
        self.assertEqual(sched.SWAP_IDLE, 600)
        self.assertEqual(sched.ORDER, ("ingest", "brief", "lint"))
        self.assertEqual(sched.DAYS[6], "sun")


if __name__ == "__main__":
    unittest.main()
