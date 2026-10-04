"""A cross-process lock per ember folder: <root>/.ember.lock.

Store.abort_running assumes it is the only runner of a job, so anything that
runs jobs (the CLI, the panel's scheduler) holds this lock around them:

    with lock.held(root):
        jobs.ingest(...)

The file is created with O_EXCL and holds {"pid", "since"}. A lock whose pid
is dead, whose since is older than MAX_AGE, or that is unreadable and older
than GARBAGE_AGE is stale: it is removed and the create is retried once.
Inside one process a module-level set refuses a second hold of the same root
(our own pid is alive, so the file alone cannot tell).
"""
import contextlib, datetime as dt, json, os, sys, threading, time

LOCK_NAME   = ".ember.lock"
MAX_AGE     = dt.timedelta(hours=12)   # no run is that long: the holder is hung or the pid reused
GARBAGE_AGE = 60                       # seconds an unreadable lock may be mid-write
MAX_READ    = 4096

_mutex = threading.Lock()
_held = set()                          # normcased absolute roots held by this process


class Busy(Exception):
    """Another live holder has the lock. pid is None when the file is unreadable."""

    def __init__(self, pid=None, since=""):
        self.pid, self.since = pid, since or ""
        who = f"pid {pid}" if pid is not None else "an unknown process"
        super().__init__(f"ember is locked by {who}" + (f" since {self.since}" if self.since else ""))


def _valid_pid(pid):
    return isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid <= 0xFFFFFFFF


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
    _k32 = None

    def _kernel32():
        global _k32
        if _k32 is None:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            k.OpenProcess.restype = wintypes.HANDLE
            k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            k.GetExitCodeProcess.restype = wintypes.BOOL
            k.CloseHandle.argtypes = (wintypes.HANDLE,)
            k.CloseHandle.restype = wintypes.BOOL
            _k32 = k
        return _k32

    def pid_alive(pid):
        """True if a process with this pid exists. Never os.kill here: on Windows
        that is TerminateProcess."""
        if not _valid_pid(pid):
            return False
        k = _kernel32()
        h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED   # exists, just not ours
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True                    # opened but unreadable: assume alive (fail closed)
            return code.value == _STILL_ACTIVE
        finally:
            k.CloseHandle(h)
else:
    def pid_alive(pid):
        """True if a process with this pid exists (signal 0 only probes)."""
        if not _valid_pid(pid):
            return False
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True                        # exists, owned by someone else
        except (OSError, OverflowError):
            return False
        return True


def _read(path):
    """(raw bytes, pid, since); pid is None when the content is not a valid lock.
    raw is None when the file is gone."""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_READ)
    except FileNotFoundError:
        return None, None, ""
    except OSError:
        return b"", None, ""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw, None, ""
    if not isinstance(data, dict) or not _valid_pid(data.get("pid")) \
            or not isinstance(data.get("since"), str):
        return raw, None, ""
    return raw, data["pid"], data["since"]


def _since_too_old(since):
    try:
        t = dt.datetime.fromisoformat(since)
    except ValueError:
        return False
    if t.tzinfo is not None:
        t = t.astimezone().replace(tzinfo=None)
    return dt.datetime.now() - t > MAX_AGE


def _stale(path, raw, pid, since):
    """Whether the lock read as (raw, pid, since) can be broken."""
    if raw is None:
        return True                            # vanished meanwhile: just retry the create
    if pid is None:
        try:
            return time.time() - os.path.getmtime(path) > GARBAGE_AGE
        except OSError:
            return True
    if pid == os.getpid():
        return True                            # not in _held, so a leftover with a reused pid
    return not pid_alive(pid) or _since_too_old(since)


def _break(path, raw):
    """Remove a stale lock, but only if it still has the content judged stale."""
    if raw is None:
        return
    if _read(path)[0] != raw:
        return                                 # replaced meanwhile; the retry will see it
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def _create(path):
    """Create the lock file; the content on success, None if it already exists."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None
    except PermissionError:
        if os.path.exists(path):               # Windows: a lock file pending deletion
            return None
        raise
    since = dt.datetime.now().isoformat(timespec="seconds")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "since": since}, f)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise
    return since


def _acquire(path):
    for attempt in range(2):
        if _create(path) is not None:
            return
        raw, pid, since = _read(path)
        if attempt == 0 and _stale(path, raw, pid, since):
            _break(path, raw)
            continue
        raise Busy(pid, since)


def _release(path):
    if _read(path)[1] == os.getpid():          # someone may have broken and retaken it
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


@contextlib.contextmanager
def held(root):
    """Hold the ember's lock for the with-block; raises Busy if someone else has it."""
    key = os.path.normcase(os.path.abspath(root))
    path = os.path.join(root, LOCK_NAME)
    with _mutex:
        if key in _held:
            raise Busy(os.getpid(), _read(path)[2])
        _held.add(key)
    try:
        _acquire(path)
    except BaseException:
        with _mutex:
            _held.discard(key)
        raise
    try:
        yield
    finally:
        try:
            _release(path)
        finally:
            with _mutex:
                _held.discard(key)
