"""Crash-safe file writes.

Every file LlamaForge owns (config.json, models.ini, stats.json) is a small
document rewritten in full. A plain truncating write means a crash, a power
loss, or a full disk mid-write leaves a half-file - and for models.ini that
loses every model the router knows about.

The pattern: write a sibling temp file, fsync it, then os.replace() onto the
target. os.replace is atomic on POSIX and on Windows (MoveFileEx with
REPLACE_EXISTING), so a reader ever sees the old file or the new one, never a
partial one.

The temp name carries pid+counter rather than a fixed `.tmp` suffix: two
LlamaForge instances (or a stray second dashboard) writing the same file would
otherwise collide on the scratch path and fail with a sharing violation on
Windows. That makes the write safe on its own, independent of any in-process
lock. Pure stdlib.
"""
import itertools, os, random, threading, time

_seq = itertools.count()
_pid = os.getpid()

# os.replace is atomic on both platforms, but on Windows it is not *contention
# free*: MoveFileEx fails with PermissionError/WinError 5 or 32 when anything
# else holds the destination for even a moment. In practice that is another
# writer replacing the same file, or - far more common on a user's machine -
# Defender or the search indexer opening the file we just wrote. POSIX rename
# has no such failure mode, so this retry is a no-op there.
# A deadline rather than an attempt count: what we are waiting out is "something
# else is holding this file for a moment", and an AV scan can hold one for a
# second or more. Jittered backoff so simultaneous writers don't retry in step.
_REPLACE_DEADLINE = 5.0     # seconds to keep trying before giving up
_REPLACE_BACKOFF = 0.005    # initial delay; doubles, capped at 0.1


def _tmp_name(path):
    return f"{path}.{_pid}.{threading.get_ident()}.{next(_seq)}.tmp"


def _replace_with_retry(tmp, path):
    deadline = time.monotonic() + _REPLACE_DEADLINE
    delay = _REPLACE_BACKOFF
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay * (0.5 + random.random()))
            delay = min(delay * 2, 0.1)


def write_text(path, text, encoding="utf-8", newline=""):
    """Atomically replace `path` with `text`. Never writes a BOM."""
    tmp = _tmp_name(path)
    try:
        with open(tmp, "w", encoding=encoding, newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)          # don't litter scratch files on failure
        except OSError:
            pass
        raise


def write_json(path, obj, indent=2):
    import json
    write_text(path, json.dumps(obj, indent=indent))
