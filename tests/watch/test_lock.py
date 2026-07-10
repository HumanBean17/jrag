"""Tests for watch/lock.py — project pidfile + flock mutual exclusion.

These tests exercise ProjectLock against a real filesystem and a real
``fcntl.flock``. They therefore run only where ``fcntl`` exists; the
``WatchUnsupportedPlatform`` path is covered by monkeypatching ``fcntl`` to
``None`` (simulating a non-Unix platform).

Each test uses a unique ``tmp_path`` so the derived pid path (a function of
``project_key(index_dir)``) is distinct per test — no cross-test pidfile
collisions in the shared per-user runtime dir.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from java_codebase_rag.watch import lock as lock_mod
from java_codebase_rag.watch.lock import (
    LockHeldError,
    ProjectLock,
    WatchUnsupportedPlatform,
)
from java_codebase_rag.watch.paths import pid_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_index(tmp_path: Path) -> Path:
    """Return a fresh index dir under tmp_path."""
    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True)
    return index_dir


def _dead_pid() -> int:
    """Return a pid guaranteed to be dead (and reaped) by the time we use it.

    We spawn a child that exits immediately, ``wait()`` for it so the OS reaps
    it, then return its pid. The pid is therefore not in use (recycling within
    the microsecond window of a test is astronomically unlikely).
    """
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


@pytest.fixture
def first_lock(tmp_path):
    """A ProjectLock that has acquired, released automatically at teardown."""
    lock = ProjectLock(_make_index(tmp_path))
    lock.acquire()
    try:
        yield lock
    finally:
        # release() is idempotent enough: closing an already-closed handle is
        # guarded internally; if the test already released, this is a no-op.
        try:
            lock.release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# (a) acquire succeeds and writes the current pid
# ---------------------------------------------------------------------------


class TestAcquire:
    def test_acquire_writes_current_pid(self, tmp_path):
        """ProjectLock(index).acquire() succeeds and the pid file holds getpid()."""
        index_dir = _make_index(tmp_path)
        lock = ProjectLock(index_dir)

        lock.acquire()

        expected_pid_path = pid_path(index_dir)
        assert expected_pid_path.exists(), "pid file not created by acquire"
        contents = expected_pid_path.read_text().strip()
        assert contents == str(os.getpid()), (
            f"pid file has {contents!r}, expected {os.getpid()!r}"
        )
        lock.release()


# ---------------------------------------------------------------------------
# (b) a second lock on the same index dir is rejected with the holder's pid
# ---------------------------------------------------------------------------


class TestMutualExclusion:
    def test_second_acquire_raises_with_holder_pid(self, first_lock, tmp_path):
        """A second ProjectLock on the same index dir raises LockHeldError whose
        .pid equals the first holder's pid and .path is the pid file."""
        # Re-derive the same index dir path the fixture created. The fixture
        # used tmp_path/"index"; we reuse the exact same path so the pid file
        # (keyed on the resolved path) is identical.
        index_dir = tmp_path / "index"
        second = ProjectLock(index_dir)

        with pytest.raises(LockHeldError) as excinfo:
            second.acquire()

        err = excinfo.value
        assert err.pid == os.getpid(), (
            f"LockHeldError.pid={err.pid!r}, expected holder pid {os.getpid()!r}"
        )
        assert err.path == pid_path(index_dir), (
            f"LockHeldError.path={err.path!r}, expected {pid_path(index_dir)!r}"
        )
        assert isinstance(err.path, Path), "LockHeldError.path must be a Path"


# ---------------------------------------------------------------------------
# (c) after release, a second lock acquires cleanly
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_allows_second_acquire(self, first_lock, tmp_path):
        """After the first holder releases, a second lock acquires cleanly."""
        first_lock.release()

        index_dir = tmp_path / "index"
        second = ProjectLock(index_dir)
        second.acquire()  # must not raise
        try:
            assert pid_path(index_dir).read_text().strip() == str(os.getpid())
        finally:
            second.release()

    def test_release_unlinks_pid_file(self, first_lock, tmp_path):
        """release() removes the pid file when it still holds our pid."""
        index_dir = tmp_path / "index"
        pid_file = pid_path(index_dir)
        assert pid_file.exists(), "precondition: pid file should exist while held"

        first_lock.release()

        assert not pid_file.exists(), "pid file should be unlinked after release"


# ---------------------------------------------------------------------------
# (d) read_holder: live pid while held, None after release
# ---------------------------------------------------------------------------


class TestReadHolder:
    def test_read_holder_returns_live_pid_while_held(self, first_lock, tmp_path):
        """read_holder returns the holder's pid while the lock is held."""
        index_dir = tmp_path / "index"
        assert ProjectLock.read_holder(index_dir) == os.getpid()

    def test_read_holder_returns_none_after_release(self, first_lock, tmp_path):
        """read_holder returns None once the pid file is gone (after release)."""
        first_lock.release()

        index_dir = tmp_path / "index"
        assert ProjectLock.read_holder(index_dir) is None


# ---------------------------------------------------------------------------
# (e) a pid file holding a dead pid is treated as stale
# ---------------------------------------------------------------------------


class TestStalePid:
    def test_read_holder_treats_dead_pid_as_stale(self, tmp_path):
        """read_holder returns None when the pid file names a dead process."""
        index_dir = _make_index(tmp_path)
        pid_file = pid_path(index_dir)
        dead = _dead_pid()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{dead}\n")

        assert ProjectLock.read_holder(index_dir) is None, (
            f"read_holder should return None for dead pid {dead}"
        )

    def test_acquire_cleans_stale_pid_file(self, tmp_path):
        """Acquiring when the pid file names a dead (stale) pid succeeds and
        overwrites it with the current pid."""
        index_dir = _make_index(tmp_path)
        pid_file = pid_path(index_dir)
        dead = _dead_pid()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{dead}\n")

        lock = ProjectLock(index_dir)
        lock.acquire()  # must not raise despite the stale pid file
        try:
            assert pid_file.read_text().strip() == str(os.getpid())
        finally:
            lock.release()


# ---------------------------------------------------------------------------
# is_holder
# ---------------------------------------------------------------------------


class TestIsHolder:
    def test_is_holder_false_before_acquire(self, tmp_path):
        """A fresh ProjectLock (never acquired) is not the holder."""
        lock = ProjectLock(_make_index(tmp_path))
        assert lock.is_holder() is False

    def test_is_holder_true_after_acquire(self, first_lock, tmp_path):
        """After acquire(), is_holder() is True."""
        assert first_lock.is_holder() is True

    def test_is_holder_false_after_release(self, first_lock, tmp_path):
        """After release(), is_holder() is False."""
        first_lock.release()
        # first_lock is now a non-holder
        assert first_lock.is_holder() is False


# ---------------------------------------------------------------------------
# LockHeldError attributes
# ---------------------------------------------------------------------------


class TestLockHeldErrorAttributes:
    def test_attributes_exposed(self, tmp_path):
        """LockHeldError exposes .pid and .path as set by the constructor."""
        path = tmp_path / "p.pid"
        err = LockHeldError(pid=12345, path=path)
        assert err.pid == 12345
        assert err.path == path

    def test_pid_may_be_none(self, tmp_path):
        """LockHeldError.pid is None when the holder pid is unreadable."""
        path = tmp_path / "p.pid"
        err = LockHeldError(pid=None, path=path)
        assert err.pid is None
        assert err.path == path


# ---------------------------------------------------------------------------
# WatchUnsupportedPlatform — constructor raises when fcntl is unavailable
# ---------------------------------------------------------------------------


class TestUnsupportedPlatform:
    def test_constructor_raises_when_fcntl_missing(self, tmp_path, monkeypatch):
        """When the fcntl import failed (fcntl is None), constructing ProjectLock
        raises WatchUnsupportedPlatform rather than crashing later."""
        monkeypatch.setattr(lock_mod, "fcntl", None)

        with pytest.raises(WatchUnsupportedPlatform):
            ProjectLock(_make_index(tmp_path))
