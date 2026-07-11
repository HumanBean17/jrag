"""Project-level pidfile + ``fcntl.flock`` mutual exclusion for ``jrag watch``.

``ProjectLock`` is the single-writer primitive the watch daemon (Task 11)
acquires so that at most one watcher — and no concurrent manual
``java-codebase-rag increment`` — runs per project.

The lock is keyed on the project's index dir: the pidfile path comes from
``paths.pid_path(index_dir)`` (Task 3). We hold an advisory exclusive flock on
that file for the lifetime of the ``ProjectLock`` object; the file also carries
the holder's pid so other tools can report who holds it (``read_holder``).

Unix-only by design. The ``fcntl`` import is guarded so that, on a platform
without it, constructing a ``ProjectLock`` fails cleanly with
``WatchUnsupportedPlatform`` instead of crashing at import time.
"""

import os
from pathlib import Path

try:  # Unix-only; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests.
    fcntl = None

from .paths import pid_path


def _read_pid_file(path: Path):
    """Return the integer pid in ``path``, or ``None``.

    ``None`` covers: missing file, empty contents, or non-integer contents.
    """
    try:
        raw = path.read_text().strip()
    except (FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class LockHeldError(Exception):
    """Raised by ``ProjectLock.acquire`` when the project lock is held elsewhere.

    Attributes:
        pid: the pid currently recorded in the pid file, or ``None`` if it could
            not be read/parsed (e.g. the file was empty).
        path: the pid file ``Path``.
    """

    def __init__(self, pid, path: Path):
        self.pid = pid
        self.path = path
        super().__init__(f"project lock held by pid={pid!r} at {path}")


class WatchUnsupportedPlatform(Exception):
    """Raised when the host platform lacks ``fcntl`` (the daemon is Unix-only)."""


class ProjectLock:
    """Exclusive per-project lock backed by a pidfile + ``fcntl.flock``.

    The flock is held for the lifetime of the object: ``acquire`` opens the pid
    file and takes ``LOCK_EX | LOCK_NB`` on it, retaining the file handle;
    ``release`` closes that handle (which releases the flock) and unlinks the
    pid file if it still records our pid.
    """

    def __init__(self, index_dir: Path):
        if fcntl is None:
            raise WatchUnsupportedPlatform(
                "jrag watch requires fcntl, which is unavailable on this platform"
            )
        self.index_dir = index_dir
        self.pid_path: Path = pid_path(index_dir)
        self._fh = None  # retained file handle while we hold the flock

    # ------------------------------------------------------------------
    # pid file helpers
    # ------------------------------------------------------------------

    def _read_pid(self):
        """Return the integer pid recorded in our pid file, or ``None``."""
        return _read_pid_file(self.pid_path)

    # ------------------------------------------------------------------
    # acquire / release
    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Take the exclusive lock, writing our pid into the pid file.

        Opens the pid file read/write (creating it if absent), then takes
        ``LOCK_EX | LOCK_NB``. If another holder blocks us, raise
        ``LockHeldError`` carrying the pid recorded in the file (or ``None`` if
        unreadable). On success, (over)write our pid and keep the handle open.
        """
        # "r+" preserves an existing holder's pid so we can report it on
        # contention; fall back to "w+" only when the file does not yet exist
        # (so we never truncate a real pid file before reading it).
        try:
            fh = open(self.pid_path, "r+")
        except FileNotFoundError:
            fh = open(self.pid_path, "w+")

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise LockHeldError(self._read_pid(), self.pid_path)
        except OSError:
            # Unexpected flock failure — don't leak the handle.
            fh.close()
            raise

        # We hold the lock: record our pid (overwriting any stale value).
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        """Release the flock and, if the pid file still names us, unlink it.

        Closing the handle releases the flock. We only unlink the pid file when
        its contents still equal our pid, so we never clobber a successor's file
        if they acquired between our close and our unlink.
        """
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

        if self._read_pid() == os.getpid():
            try:
                self.pid_path.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    def is_holder(self) -> bool:
        """True iff this lock currently holds the project lock.

        Implementation note on flock semantics: ``flock`` treats separate file
        descriptors even within a single process as independent holders, so a
        probe handle CANNOT acquire ``LOCK_EX | LOCK_NB`` while our retained
        handle holds it. The probe therefore FAILS exactly when the lock is
        held (by us or another). Combined with the pid file recording our own
        pid, that means: probe blocked AND our pid on disk => we are the holder.

        (The prose in the task brief said the probe would "succeed"; on Unix
        flock the polarity is inverted. See the task-2 report.)
        """
        try:
            probe = open(self.pid_path, "r+")
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Held by someone; it is us iff our pid is on disk.
                return self._read_pid() == os.getpid()
            # Probe acquired the lock => it was free => we do not hold it.
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            return False
        finally:
            probe.close()

    @classmethod
    def read_holder(cls, index_dir: Path):
        """Return the live holder's pid, or ``None`` if the lock is free/stale.

        Reads the pid file and returns the recorded pid iff a process with that
        pid is currently alive (``os.kill(pid, 0)``); otherwise ``None`` (file
        missing, unreadable, or naming a dead/stale pid). Does NOT take or test
        the flock, and does not require ``fcntl``.
        """
        path = pid_path(index_dir)
        pid = _read_pid_file(path)
        if pid is None:
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            # No such process => stale.
            return None
        except PermissionError:
            # The pid exists but is not ours to signal — it is alive, just owned
            # by another user, so it is a legitimate holder.
            return pid
        return pid
