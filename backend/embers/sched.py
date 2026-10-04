"""Ember scheduling decisions: pure functions, no I/O, no clock reads.

The scheduler thread asks two questions on every tick: which of an ember's
jobs are due, and what to do about the router right now (run, swap the
model, wait, or give up). Both are answered here from plain values so they
can be tested without threads or a live router. All datetimes are naive
local time.
"""
from datetime import timedelta

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
    """
    out = []
    for job in ORDER:
        spec = jobs_conf.get(job)
        if not isinstance(spec, str):
            continue
        occ = last_occurrence(spec, now)
        if occ is None or occ <= created:
            continue
        last = last_attempts.get(job)
        if last is None or last < occ:
            out.append(job)
    return out


def _decide(want, loaded, busy, idle_for, swap_allowed):
    if busy is None:
        return "wait", "the router is not reachable"
    if busy > 0:
        return "wait", "the router is busy"
    if not want:
        return ("run", loaded) if loaded else ("wait", "no model is loaded")
    if want == loaded:
        return "run", want
    if not swap_allowed:
        return "wait", f"needs {want}; model swapping is off"
    if idle_for >= SWAP_IDLE:
        return "swap", want
    return "wait", f"waiting for 10 idle minutes before loading {want}"


def decide(want, loaded, busy, idle_for, waited, swap_allowed):
    """One of ('run', model), ('swap', model), ('wait', reason), ('skip', reason)."""
    if not isinstance(want, str):
        want = ""
    action, arg = _decide(want, loaded, busy, idle_for, swap_allowed)
    if action == "wait" and waited >= WAIT_MAX:
        return "skip", f"{arg} for 60 minutes"
    return action, arg
