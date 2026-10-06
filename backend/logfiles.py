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
* trim() rotates a log while its process runs (the panel calls trim_all()
  every minute): copy it to `<log>.1`, then truncate it in place. That is only
  safe if every writer appends - one that writes at its own offset would carry
  on past the new end and pad the file with NULs. POSIX `"a"` and `>>` are
  O_APPEND. On Windows a handle Python opens with "a" is not append-only once
  a child inherits it, so open_append() opens one with FILE_APPEND_DATA, and
  trim() skips a log that something else (run.ps1's redirect, an older
  panel) holds. Lines written between the copy and the truncate can be lost.

    python logfiles.py --rotate <log> [<log> ...]   (used by run.sh)
"""
import glob, os, shutil, sys, threading

MAX_BYTES = 50 * 1024 * 1024    # rotate a log once it passes this
KEEP = 7                        # old copies kept: <log>.1 .. <log>.7
TAIL_CAP = 4 * 1024 * 1024      # most a tail ever reads, whatever n asks for
BLOCK = 64 * 1024
TRIM_EVERY = 60                 # seconds between trim_all() passes
# Logs a long-running process appends to (build logs restart on each run).
WATCHED = ("router.out.log", "router.err.log", "vllm.out.log", "vllm.err.log",
           "slot-*.log")


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
        _shift(path, keep)
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def _shift(path, keep):
    for i in range(keep - 1, 0, -1):
        older = f"{path}.{i}"
        if os.path.exists(older):
            os.replace(older, f"{path}.{i + 1}")


if os.name == "nt":
    import ctypes, msvcrt
    from ctypes import wintypes
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _INVALID = wintypes.HANDLE(-1).value
    _SHARE_ALL = 0x1 | 0x2 | 0x4                 # read | write | delete
    _FILE_APPEND_DATA, _SYNCHRONIZE, _DELETE = 0x4, 0x100000, 0x10000
    _OPEN_EXISTING, _OPEN_ALWAYS, _NORMAL = 3, 4, 0x80

    def _create(path, access, disposition):
        h = _k32.CreateFileW(os.path.abspath(path), access, _SHARE_ALL, None,
                             disposition, _NORMAL, None)
        return None if h in (None, _INVALID) else h

    def _append_handle(path):
        """A text file whose handle can only append - a child that inherits
        it writes at the end even after the file is truncated under it."""
        h = _create(path, _FILE_APPEND_DATA | _SYNCHRONIZE, _OPEN_ALWAYS)
        if h is None:
            return None
        try:
            fd = msvcrt.open_osfhandle(h, os.O_WRONLY | os.O_APPEND)
        except OSError:
            _k32.CloseHandle(h)
            return None
        return os.fdopen(fd, "a", encoding="utf-8", errors="replace")

    def _append_only_writers(path):
        """Opening for DELETE succeeds only if every open handle shares
        delete - which only _append_handle()'s do (Python's open(), and
        PowerShell's Start-Process redirect, refuse). Nothing is deleted."""
        h = _create(path, _DELETE, _OPEN_EXISTING)
        if h is None:
            return False
        _k32.CloseHandle(h)
        return True
else:
    def _append_handle(path):
        return None                     # open(path, "a") is O_APPEND already

    def _append_only_writers(path):
        return True                     # our writers: Python "a", run.sh's >>


def open_append(path, max_bytes=MAX_BYTES, keep=KEEP):
    """rotate(), then open path for a child process to append to."""
    rotate(path, max_bytes, keep)
    return _append_handle(path) or open(path, "a", encoding="utf-8", errors="replace")


def trim(path, max_bytes=MAX_BYTES, keep=KEEP):
    """Rotate a log a running process may still be appending to: copy it to
    path.1 (shifting older copies), then empty it. True if it was trimmed;
    never raises."""
    try:
        if os.path.getsize(path) <= max_bytes or not _append_only_writers(path):
            return False
        _shift(path, keep)
        shutil.copyfile(path, f"{path}.1")
        with open(path, "r+b") as f, open(f"{path}.1", "ab") as old:
            f.seek(old.tell())                   # what was written during the copy
            shutil.copyfileobj(f, old)
            f.truncate(0)
        return True
    except OSError:
        return False


def trim_all(logdir, max_bytes=MAX_BYTES, keep=KEEP):
    for pattern in WATCHED:
        for path in glob.glob(os.path.join(logdir, pattern)):
            trim(path, max_bytes, keep)


def start_trimmer(logdir, every=TRIM_EVERY):
    """A daemon thread that runs trim_all(logdir) every `every` seconds."""
    def loop():
        while True:
            trim_all(logdir)
            if stop.wait(every):
                return
    stop = threading.Event()
    threading.Thread(target=loop, daemon=True, name="log-trimmer").start()
    return stop


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--rotate":
        sys.exit("usage: logfiles.py --rotate <log> [<log> ...]")
    for p in sys.argv[2:]:
        rotate(p)
