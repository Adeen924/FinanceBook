"""
Lock file that stops one database being edited on two computers at once.

The lock is a small JSON file next to the database (``finances.db.lock``).
When the database lives in a OneDrive / Google Drive folder the lock syncs
with it, so the other laptop sees it — but only after the sync service has
copied it across. That makes this lock BEST-EFFORT: it reliably catches a
second copy of FinanceBook on the same computer, and catches the other laptop
once the cloud folder has finished syncing. Let OneDrive finish syncing before
opening the database on the other computer.

Pure Python (no Qt), so it can be unit-tested and used before the window
exists. The Qt side (heartbeat timer, dialogs) lives in ui/connect_dialog.py
and ui/main_window.py.
"""
import atexit
import json
import os
import sys
import time
import uuid
from datetime import datetime

# How often the owner refreshes the lock's heartbeat. Every write is synced by
# OneDrive, so this is kept well above "seconds".
HEARTBEAT_SECONDS = 90
# A lock whose heartbeat is older than this is treated as abandoned (the other
# laptop went to sleep, crashed, or lost power) and can be taken without asking.
STALE_AFTER_SECONDS = 12 * 60

LOCK_SUFFIX = ".lock"

# Locks this process holds, released at interpreter exit even if the window
# never got a closeEvent (an error during startup, sys.exit from a dialog…).
_LIVE: set = set()


def _release_all():
    for lock in list(_LIVE):
        lock.release()


atexit.register(_release_all)


def lock_path(db_path: str) -> str:
    return os.path.abspath(db_path) + LOCK_SUFFIX


def machine_name() -> str:
    # FINANCEBOOK_MACHINE_NAME is a test-only override for simulating two laptops.
    name = os.environ.get("FINANCEBOOK_MACHINE_NAME") or os.environ.get("COMPUTERNAME")
    if not name:
        try:
            import socket
            name = socket.gethostname()
        except Exception:
            name = "unknown"
    return name


def user_name() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def _app_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
                  encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) would TERMINATE the process on Windows — ask the OS instead.
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            # Access denied means it exists but belongs to someone else.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_lock(db_path: str) -> dict | None:
    """
    The current lock's contents: None if there is no lock file, or
    {"corrupt": True} if it exists but can't be parsed (empty, half-written,
    mangled by a sync conflict…). A just-created lock may be read before its
    owner has written it, so an unreadable file is re-read briefly first.
    """
    path = lock_path(db_path)
    for attempt in range(3):
        try:
            with open(path, "r", encoding="utf-8") as f:
                info = json.load(f)
            if isinstance(info, dict) and info.get("session_id"):
                return info
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            pass
        if attempt < 2:
            time.sleep(0.15)
    return {"corrupt": True}


def heartbeat_age(info: dict, now: float | None = None) -> float:
    """Seconds since the lock's last heartbeat (0 if the clock says the future)."""
    now = time.time() if now is None else now
    try:
        return max(0.0, now - float(info.get("last_heartbeat", 0)))
    except (TypeError, ValueError):
        return float("inf")


def is_stale(info: dict | None, now: float | None = None) -> bool:
    if not info or info.get("corrupt"):
        return True
    if heartbeat_age(info, now) > STALE_AFTER_SECONDS:
        return True
    if info.get("machine") == machine_name():
        try:
            return not pid_alive(int(info.get("pid", 0)))
        except (TypeError, ValueError):
            return True
    return False


def describe(info: dict, now: float | None = None) -> str:
    """'In use on LAPTOP-2 by sam since 3 Nov 09:14 (last active 2 min ago)'."""
    try:
        since = datetime.fromtimestamp(float(info.get("acquired_at", 0))).strftime("%d %b %H:%M")
    except (TypeError, ValueError, OSError):
        since = "an unknown time"
    mins = int(heartbeat_age(info, now) // 60)
    ago = "just now" if mins < 1 else f"{mins} min ago"
    return (f"In use on {info.get('machine', 'another computer')} by "
            f"{info.get('user', 'someone')} since {since} (last active {ago})")


class LockHeld(Exception):
    """Another live session holds the lock. `info` is its lock file contents."""
    def __init__(self, info: dict):
        super().__init__(describe(info))
        self.info = info

    @property
    def same_machine(self) -> bool:
        return self.info.get("machine") == machine_name()


class DbLock:
    """
    One session's claim on a database. Typical use:

        lock = DbLock(path)
        lock.acquire()          # raises LockHeld if someone else is using it
        ...  lock.heartbeat()   # every HEARTBEAT_SECONDS; False = we lost it
        lock.release()
    """
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self.path = lock_path(db_path)
        self.session_id = uuid.uuid4().hex
        self.owned = False
        self.acquired_at = 0.0
        self.last_beat = 0.0

    def _payload(self) -> dict:
        now = time.time()
        return {"machine": machine_name(), "user": user_name(), "pid": os.getpid(),
                "app_version": _app_version(), "acquired_at": self.acquired_at or now,
                "last_heartbeat": now, "session_id": self.session_id}

    def _create_exclusive(self) -> bool:
        """Create the lock file only if none exists (atomic on one machine)."""
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        self.acquired_at = time.time()
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._payload(), f)
        return True

    def _overwrite(self):
        # Written in place rather than via a temp file + rename: a sync client
        # often has the file open, which makes a rename fail on Windows. A
        # reader that catches it half-written re-reads (see read_lock).
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._payload(), f)

    def acquire(self, force: bool = False):
        """
        Take the lock. Raises LockHeld if another live session has it, unless
        force=True (the user chose "Take Over"). A stale lock is replaced.
        """
        if self.owned:
            return
        for _ in range(3):
            if self._create_exclusive():
                self._took()
                return
            info = read_lock(self.db_path)
            if info is None:
                continue            # vanished between our create and read — retry
            if info.get("session_id") == self.session_id:
                self._took()
                return
            if force:
                self.acquired_at = time.time()
                self._overwrite()
                self._took()
                return
            if not is_stale(info):
                raise LockHeld(info)
            # Only remove the exact lock we judged stale — another instance
            # may have replaced it in the meantime.
            if read_lock(self.db_path) != info:
                continue
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            except OSError:
                # Can't remove it (e.g. read-only share) — overwrite instead.
                self.acquired_at = time.time()
                self._overwrite()
                self._took()
                return
        info = read_lock(self.db_path) or {"corrupt": True}
        raise LockHeld(info)

    def _took(self):
        self.owned = True
        self.last_beat = time.time()
        _LIVE.add(self)

    def heartbeat(self) -> bool:
        """
        Refresh our heartbeat. Returns False if the lock now belongs to
        someone else (they took over) — the caller must stop writing.
        If our lock file was deleted from under us, it is recreated.
        """
        if not self.owned:
            return False
        info = read_lock(self.db_path)
        try:
            if info is None:
                # Deleted from under us: recreate it, unless someone beat us to it.
                if not self._create_exclusive():
                    info = read_lock(self.db_path)
                    if not info or info.get("session_id") != self.session_id:
                        self.owned = False
                        _LIVE.discard(self)
                        return False
                self.last_beat = time.time()
                return True
            if info.get("corrupt"):
                # Mangled by a sync conflict — it isn't anyone else's, so reclaim it.
                self._overwrite()
                self.last_beat = time.time()
                return True
        except OSError:
            return True             # transient (sync client busy) — try next tick
        if info.get("session_id") != self.session_id:
            self.owned = False
            _LIVE.discard(self)
            return False
        try:
            self._overwrite()
        except OSError:
            return True             # transient (sync client busy) — try next tick
        self.last_beat = time.time()
        return True

    def still_ours(self) -> bool:
        """Check without writing."""
        if not self.owned:
            return False
        info = read_lock(self.db_path)
        return bool(info) and info.get("session_id") == self.session_id

    def release(self):
        """Remove the lock file if it is still ours. Safe to call repeatedly."""
        if not self.owned:
            return
        self.owned = False
        _LIVE.discard(self)
        info = read_lock(self.db_path)
        if info and info.get("session_id") == self.session_id:
            try:
                os.remove(self.path)
            except OSError:
                pass
