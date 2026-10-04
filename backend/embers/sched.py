"""Ember scheduling decisions: pure functions, no I/O, no clock reads.

The scheduler thread asks two questions on every tick: which of an ember's
jobs are due, and what to do about the router right now (run, swap the
model, wait, or give up). Both are answered here from plain values so they
can be tested without threads or a live router. All datetimes are naive
local time.
"""
import math
from datetime import datetime, timedelta

from .templates import WHEN_RE

WAIT_MAX  = 3600      # s a due job may wait for the router before it is skipped
SWAP_IDLE = 600       # s of observed router idleness before a model swap is allowed
DAYS  = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ORDER = ("ingest", "brief", "lint")


def parse_when(spec):
    """'07:00' -> (None, 7, 0); 'sun 03:00' -> (6, 3, 0); anything else -> None."""
    if not isinstance(spec, str):
        return None
    m = WHEN_RE.fullmatch(spec)
    if not m:
        return None
    day = DAYS.index(m.group(1)) if m.group(1) else None
    return day, int(m.group(2)), int(spec[-2:])


def last_occurrence(spec, now):
    """The latest time matching spec that is <= now, or None for off/bad."""
    p = parse_when(spec)
    if p is None:
        return None
    day, hh, mm = p
    occ = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if day is None:
        return occ if occ <= now else occ - timedelta(days=1)
    occ -= timedelta(days=(now.weekday() - day) % 7)
    return occ if occ <= now else occ - timedelta(days=7)


def next_occurrence(spec, now):
    """The earliest time matching spec that is > now, or None for off/bad."""
    last = last_occurrence(spec, now)
    if last is None:
        return None
    # Slots are fixed wall-clock times, so the next one is a period after the last.
    return last + timedelta(days=1 if parse_when(spec)[0] is None else 7)


def due_jobs(jobs_conf, last_attempts, created, now):
    """Jobs whose latest slot is after creation and not yet attempted, in ORDER.

    Only the latest slot counts: a panel that was off for days runs each job
    once, and a brand-new ember waits for its first slot.

    Datetimes only: the caller parses timestamps. Inputs come from the
    user-editable ember.json, so junk is tolerated. A non-dict jobs_conf or
    last_attempts, or a created that isn't a datetime, means nothing is due
    (we can't tell the ember's age). A last attempt that isn't a datetime
    counts as never attempted.
    """
    if not (isinstance(jobs_conf, dict) and isinstance(last_attempts, dict)
            and isinstance(created, datetime) and isinstance(now, datetime)):
        return []
    out = []
    for job in ORDER:
        spec = jobs_conf.get(job)
        if not isinstance(spec, str):
            continue
        occ = last_occurrence(spec, now)
        if occ is None or occ <= created:
            continue
        last = last_attempts.get(job)
        if not isinstance(last, datetime) or last < occ:
            out.append(job)
    return out


def _num(v):
    """v as a finite number, or None (bools and numeric strings are not numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return v


def _decide(want, loaded, busy, idle_for, swap_allowed):
    busy = _num(busy)
    if busy is None or busy < 0:
        return "wait", "the router is not reachable"
    if busy > 0:
        return "wait", "the router is busy"
    if not want:
        return ("run", loaded) if loaded else ("wait", "no model is loaded")
    if want == loaded:
        return "run", want
    if swap_allowed is not True:
        return "wait", f"needs {want}; model swapping is off"
    if (_num(idle_for) or 0) >= SWAP_IDLE:
        return "swap", want
    return "wait", f"waiting for {SWAP_IDLE // 60} idle minutes before loading {want}"


def decide(want, loaded, busy, idle_for, waited, swap_allowed):
    """One of ('run', model), ('swap', model), ('wait', reason), ('skip', reason).

    busy and idle_for come from /metrics, so junk is tolerated: a bad busy
    means the router is unreachable, a bad idle_for counts as 0 (never swap
    on it) and a bad waited counts as WAIT_MAX (never wait forever).
    """
    if not isinstance(want, str):
        want = ""
    if not (isinstance(loaded, str) and loaded):
        loaded = None
    action, arg = _decide(want, loaded, busy, idle_for, swap_allowed)
    waited = _num(waited)
    if action == "wait" and (waited is None or waited >= WAIT_MAX):
        return "skip", f"{arg} for {WAIT_MAX // 60} minutes"
    return action, arg
