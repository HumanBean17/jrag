"""Tests for ``raise_fd_limit`` — EMFILE / "too many open files" mitigation.

LanceDB's merge-insert path opens many file handles concurrently; under the
default OS soft ``RLIMIT_NOFILE`` (256 on macOS GUI/launchd-launched processes)
this exhausts file descriptors -> ``Too many open files (os error 24)`` in
``lance-io/local.rs``. ``raise_fd_limit`` raises the process's own soft limit
toward its hard limit so cocoindex children (which inherit rlimits) get headroom.

See https://github.com/HumanBean17/java-codebase-rag/issues/306
"""

from __future__ import annotations

import sys

import pytest

from java_codebase_rag import _fdlimit

# These tests exercise the Unix-only ``resource.RLIMIT_NOFILE`` raising path.
# ``raise_fd_limit`` no-ops on Windows (where the ``resource`` module is absent),
# so there is nothing to assert there.
pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="resource.RLIMIT_NOFILE is Unix-only; raise_fd_limit no-ops on Windows",
)


def test_raises_soft_limit_up_to_cap(monkeypatch):
    """When soft < min(hard, cap), raise soft to the target and keep hard."""
    monkeypatch.setattr(_fdlimit.resource, "getrlimit", lambda _rlim: (256, 65536))
    calls: list[tuple] = []
    monkeypatch.setattr(
        _fdlimit.resource, "setrlimit", lambda rlim, limits: calls.append((rlim, limits))
    )

    _fdlimit.raise_fd_limit(cap=4096)

    assert calls == [(_fdlimit.resource.RLIMIT_NOFILE, (4096, 65536))]


def test_caps_target_at_hard_limit(monkeypatch):
    """Never exceed the hard limit even when cap > hard."""
    monkeypatch.setattr(_fdlimit.resource, "getrlimit", lambda _rlim: (256, 1024))
    calls: list[tuple] = []
    monkeypatch.setattr(
        _fdlimit.resource, "setrlimit", lambda rlim, limits: calls.append((rlim, limits))
    )

    _fdlimit.raise_fd_limit(cap=65536)  # target = min(1024, 65536) = 1024

    assert calls == [(_fdlimit.resource.RLIMIT_NOFILE, (1024, 1024))]


def test_noop_when_soft_already_at_or_above_target(monkeypatch):
    """No setrlimit call when the soft limit is already high enough."""
    monkeypatch.setattr(_fdlimit.resource, "getrlimit", lambda _rlim: (1048576, 1048576))
    calls: list[tuple] = []
    monkeypatch.setattr(
        _fdlimit.resource, "setrlimit", lambda rlim, limits: calls.append((rlim, limits))
    )

    _fdlimit.raise_fd_limit(cap=65536)

    assert calls == []


def test_noop_when_rlimit_nofile_unsupported(monkeypatch):
    """Windows-like host with no RLIMIT_NOFILE: no error, no setrlimit."""
    monkeypatch.delattr(_fdlimit.resource, "RLIMIT_NOFILE")
    calls: list[tuple] = []
    monkeypatch.setattr(
        _fdlimit.resource, "setrlimit", lambda *a, **k: calls.append((a, k))
    )

    _fdlimit.raise_fd_limit()  # must not raise

    assert calls == []


def test_swallows_setrlimit_errors(monkeypatch):
    """Best-effort: a failing setrlimit must never propagate."""
    monkeypatch.setattr(_fdlimit.resource, "getrlimit", lambda _rlim: (256, 65536))

    def boom(rlim, limits):
        raise OSError("permission denied")

    monkeypatch.setattr(_fdlimit.resource, "setrlimit", boom)

    _fdlimit.raise_fd_limit(cap=4096)  # must not raise
