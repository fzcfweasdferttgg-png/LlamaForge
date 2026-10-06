"""Bounded reads and size-capped rotation for the logs LlamaForge appends to.

Issue #26: with `verbose = true` in models.ini, router.out.log reached 5.1 GB
and the panel grew past 50 GB of RAM, because every poll of the log view read
the whole file to keep its last 400 lines, and nothing ever trimmed the file.

* tail_lines() reads backwards from the end in blocks and stops once it has
  the lines it needs, or has read max_bytes - whichever comes first.
* rotate() runs when a process is (re)started, before its log is opened for
  append: a log over max_bytes becomes `<log>.1`, `.1` becomes `.2`, and so
  on, keeping `keep` old copies. A log held open by a process that is still
  running cannot be renamed on Windows; then the old log is appended to.

    python logfiles.py --rotate <log> [<log> ...]   (used by run.sh)
"""
import os, sys

MAX_BYTES = 50 * 1024 * 1024    # rotate a log once it passes this
KEEP = 7                        # old copies kept: <log>.1 .. <log>.7
TAIL_CAP = 4 * 1024 * 1024      # most a tail ever reads, whatever n asks for
BLOCK = 64 * 1024


def tail_lines(path, n, max_bytes=TAIL_CAP, block=BLOCK):
    """The last n lines of path, each ending in "\\n" except perhaps the last,
    like readlines()[-n:] on a text-mode file. [] if the file is missing."""
    if n <= 0:
        return []
    try:
        f = open(path, "rb")
    except OSError:
        return []
    with f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = b""
        # n+1 newlines guarantee n whole lines after the cut below.
        while pos > 0 and len(buf) < max_bytes and buf.count(b"\n") <= n:
            step = min(block, pos, max_bytes - len(buf))
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    if pos > 0:
        cut = buf.find(b"\n")
        if cut != -1:            # drop the partial line we started inside
            buf = buf[cut + 1:]
        # else one line longer than max_bytes: keep its end
    text = buf.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln + "\n" for ln in text.split("\n")]
    lines[-1] = lines[-1][:-1]           # the text after the last "\n"
    if not lines[-1]:
        lines.pop()
    return lines[-n:]


def rotate(path, max_bytes=MAX_BYTES, keep=KEEP):
    """Shift path to path.1 (dropping path.<keep>) if it is over max_bytes.
    Never raises: a log that can't be rotated is appended to as before."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return
        for i in range(keep - 1, 0, -1):
            older = f"{path}.{i}"
            if os.path.exists(older):
                os.replace(older, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def open_append(path, max_bytes=MAX_BYTES, keep=KEEP):
    """rotate(), then open path for a child process to append to."""
    rotate(path, max_bytes, keep)
    return open(path, "a", encoding="utf-8", errors="replace")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--rotate":
        sys.exit("usage: logfiles.py --rotate <log> [<log> ...]")
    for p in sys.argv[2:]:
        rotate(p)
