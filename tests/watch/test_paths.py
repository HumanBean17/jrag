"""Tests for watch/paths.py — runtime socket/pid/state path derivation."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# These imports will fail until paths.py is created
from java_codebase_rag.watch.paths import (
    runtime_dir,
    project_key,
    socket_path,
    pid_path,
    state_path,
)


class TestProjectKey:
    """Tests for project_key() — stable, distinct hash of resolved index_dir."""

    def test_project_key_stable_for_same_path(self, tmp_path):
        """project_key returns the same 12-char hex string for the same resolved path."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)

        key1 = project_key(index_dir)
        key2 = project_key(index_dir)

        assert key1 == key2, f"project_key unstable: {key1} != {key2}"
        assert isinstance(key1, str)
        assert len(key1) == 12
        # Must be lowercase hex
        assert key1.islower()
        assert all(c in "0123456789abcdef" for c in key1)

    def test_project_key_distinct_for_different_paths(self, tmp_path):
        """project_key differs for two different index dirs."""
        index_a = tmp_path / "index_a"
        index_b = tmp_path / "index_b"
        index_a.mkdir(parents=True)
        index_b.mkdir(parents=True)

        key_a = project_key(index_a)
        key_b = project_key(index_b)

        assert key_a != key_b, (
            f"project_key collision: {key_a} == {key_b} for different paths"
        )

    def test_project_key_resolves_before_hashing(self, tmp_path):
        """project_key resolves symlinks/relative paths before hashing."""
        # Create a real directory
        real_dir = tmp_path / "real_index"
        real_dir.mkdir(parents=True)

        # Create a symlink to it
        link_dir = tmp_path / "link_index"
        link_dir.symlink_to(real_dir)

        # Same resolved path should yield same key
        key_real = project_key(real_dir)
        key_link = project_key(link_dir)

        assert key_real == key_link, (
            f"project_key didn't resolve before hashing: {key_real} != {key_link}"
        )


class TestSocketPidStatePaths:
    """Tests for socket_path(), pid_path(), state_path() — distinct paths, same key."""

    def test_paths_are_distinct(self, tmp_path):
        """socket_path, pid_path, state_path return three different paths."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)

        sock = socket_path(index_dir)
        pid = pid_path(index_dir)
        state = state_path(index_dir)

        assert sock != pid != state, (
            f"paths not distinct: sock={sock}, pid={pid}, state={state}"
        )

    def test_paths_share_same_runtime_dir_parent(self, tmp_path):
        """All three paths share the same runtime_dir() parent."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)

        sock = socket_path(index_dir)
        pid = pid_path(index_dir)
        state = state_path(index_dir)

        rt_dir = runtime_dir()
        assert sock.parent == rt_dir, f"socket_path parent not runtime_dir: {sock.parent}"
        assert pid.parent == rt_dir, f"pid_path parent not runtime_dir: {pid.parent}"
        assert state.parent == rt_dir, f"state_path parent not runtime_dir: {state.parent}"

    def test_paths_share_same_key_suffix(self, tmp_path):
        """All three paths share the same project_key suffix (before extension)."""
        index_dir = tmp_path / "index"
        index_dir.mkdir(parents=True)

        sock = socket_path(index_dir)
        pid = pid_path(index_dir)
        state = state_path(index_dir)

        # Extract the key from each path (format: jrag-watch-{key}.{ext})
        sock_key = sock.stem.split("-")[-1]
        pid_key = pid.stem.split("-")[-1]
        state_key = state.stem.split("-")[-1]

        assert sock_key == pid_key == state_key, (
            f"key mismatch: sock={sock_key}, pid={pid_key}, state={state_key}"
        )

    def test_sibling_index_dirs_produce_distinct_sockets(self, tmp_path):
        """Two sibling index dirs produce distinct socket paths (no collision)."""
        index_a = tmp_path / "a"
        index_b = tmp_path / "b"
        index_a.mkdir(parents=True)
        index_b.mkdir(parents=True)

        sock_a = socket_path(index_a)
        sock_b = socket_path(index_b)

        assert sock_a != sock_b, (
            f"socket collision for sibling dirs: {sock_a} == {sock_b}"
        )


class TestRuntimeDir:
    """Tests for runtime_dir() — per-user runtime directory, created if missing."""

    def test_runtime_dir_exists_after_call(self):
        """runtime_dir returns an existing directory (creates if needed)."""
        rt = runtime_dir()

        assert rt.exists(), f"runtime_dir does not exist: {rt}"
        assert rt.is_dir(), f"runtime_dir is not a directory: {rt}"

    @patch("sys.platform", "linux")
    def test_runtime_dir_resolution_order_xdg(self, monkeypatch):
        """runtime_dir respects XDG_RUNTIME_DIR env var first (Linux)."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/xdg-runtime")

        rt = runtime_dir()
        assert rt == Path("/tmp/xdg-runtime"), (
            f"runtime_dir didn't use XDG_RUNTIME_DIR: {rt}"
        )

    def test_runtime_dir_resolution_order_tmpdir(self, monkeypatch):
        """runtime_dir falls back to TMPDIR if XDG_RUNTIME_DIR not set."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("TMPDIR", "/tmp/my-tmp")

        rt = runtime_dir()
        assert rt == Path("/tmp/my-tmp"), (
            f"runtime_dir didn't use TMPDIR: {rt}"
        )

    @patch("sys.platform", "darwin")
    def test_runtime_dir_resolution_order_macos(self, monkeypatch, tmp_path):
        """runtime_dir uses ~/Library/Caches/JragWatch on macOS if no env vars."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.delenv("TMPDIR", raising=False)

        # Mock home to a temp location for this test
        mock_home = tmp_path / "home"
        mock_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(mock_home))

        rt = runtime_dir()
        expected = mock_home / "Library" / "Caches" / "JragWatch"
        assert rt == expected, (
            f"runtime_dir didn't use macOS path: expected={expected}, got={rt}"
        )

    @patch("sys.platform", "linux")
    def test_runtime_dir_resolution_order_fallback(self, monkeypatch):
        """runtime_dir falls back to tempfile.gettempdir()/jrag-watch-{user} on Linux."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.delenv("TMPDIR", raising=False)

        rt = runtime_dir()
        # Should be tempfile.gettempdir() / "jrag-watch-{username}"
        import tempfile
        import getpass
        expected = Path(tempfile.gettempdir()) / f"jrag-watch-{getpass.getuser()}"
        assert rt == expected, (
            f"runtime_dir didn't use Linux fallback: expected={expected}, got={rt}"
        )
