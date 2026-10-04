"""A cross-process lock per ember folder, on <root>/.ember.lock.

Store.abort_running assumes it is the only runner of a job, so anything that
runs jobs (the CLI, the panel's scheduler) holds this lock around them:

    with lock.held(root):
        jobs.ingest(...)

The lock is an OS file lock (msvcrt byte-range lock on Windows, flock on
POSIX) held on an open fd for the whole block. The kernel drops it when the
process dies, so there is no stale-lock detection. The file is permanent and
never deleted; while held it says {"pid", "since"} for diagnostics only.
Within one process a module-level set refuses a second hold of the same
folder, because OS locks do not reliably exclude the process holding them.
"""
import contextlib, datetime as dt, errno, json, os, sys, threading

LOCK_NAME   = ".ember.lock"
LOCK_OFFSET = 1 << 20      # Windows: the locked byte sits past the JSON so rivals can still read it
MAX_READ    = 4096
# "Someone else holds it". Anything else (ENOLCK, EBADF...) is a real error, not Busy.
_HELD_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_mutex = threading.Lock()
_held = set()              # normcased real paths of the folders this process holds


class Busy(Exception):
    """Another holder has the lock. pid is None when its diagnostics are unreadable."""

    def __init__(self, pid=None, since="", path=""):
        self.pid, self.since, self.path = pid, since or "", path
        who = f"pid {pid}" if pid is not None else "an unknown process"
        super().__init__(f"ember is locked by {who}" + (f" since {self.since}" if self.since else ""))


def _try_lock(fd):
    """Take the exclusive lock without blocking; False if someone else has it."""
    try:
        if sys.platform == "win32":
            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in _HELD_ERRNOS:
            return False
        raise
    return True


def _unlock(fd):
    if sys.platform == "win32":
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_info(fd):
    """(pid, since) from the holder's diagnostics, best effort; (None, "") if unreadable."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        data = json.loads(os.read(fd, MAX_READ).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, ""
    if not isinstance(data, dict):
        return None, ""
    pid, since = data.get("pid"), data.get("since")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        pid = None
    return pid, since if isinstance(since, str) else ""


def _write_info(fd):
    since = dt.datetime.now().isoformat(timespec="seconds")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps({"pid": os.getpid(), "since": since}).encode("utf-8"))


def _release(fd):
    """Clear the diagnostics, unlock, close. Never raises: it runs in a finally."""
    for step in (lambda: os.ftruncate(fd, 0), lambda: _unlock(fd), lambda: os.close(fd)):
        try:
            step()
        except OSError:
            pass


@contextlib.contextmanager
def held(root):
    """Hold the ember's lock for the with-block; raises Busy if someone else has it."""
    key = os.path.normcase(os.path.realpath(root))
    path = os.path.join(root, LOCK_NAME)
    with _mutex:
        if key in _held:
            raise Busy(os.getpid(), "", path)
        _held.add(key)
    fd, locked = None, False
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        locked = _try_lock(fd)
        if not locked:
            pid, since = _read_info(fd)
            raise Busy(pid, since, path)
        try:
            _write_info(fd)
        except OSError:
            pass           # diagnostics only; the lock itself is what matters
    except BaseException:  # including KeyboardInterrupt: never leave the fd (or the lock) behind
        if locked:
            _release(fd)
        elif fd is not None:
            try:
                os.close(fd)   # not ours: leave the holder's diagnostics alone
            except OSError:
                pass
        with _mutex:
            _held.discard(key)
        raise
    try:
        yield
    finally:
        _release(fd)
        with _mutex:
            _held.discard(key)
