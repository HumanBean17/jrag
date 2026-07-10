"""Tests for ``java_codebase_rag.pipeline`` subprocess helpers.

Focus: ``run_cocoindex_update`` drops the Lance target tables before a *full
reprocess* so the update takes the fast INSERT path. The in-place alternative
(cocoindex's bulk-update ``merge_insert``) emits ~one deletion-vector + version
commit per matched row — O(rows) of tiny file IO that hangs for many minutes on
large repos. Drop+recreate is identical output for a full rebuild.

Also covers ``_popen_capturing_stderr``'s Ctrl+C behavior: it must wait on the
child (interruptible) before joining the drain threads (not interruptible), and
on abort must terminate the child and re-raise WITHOUT joining.
"""
from __future__ import annotations

import subprocess
import sys
import threading

from java_codebase_rag import pipeline


def _ok() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _stub_impl(monkeypatch, seen: dict) -> None:
    """Replace the impl + post-optimize with no-op stubs (no cocoindex/lancedb)."""

    def fake_impl(env, **kwargs):
        seen["update"] = seen.get("update", 0) + 1
        seen["full_reprocess"] = kwargs.get("full_reprocess")
        return _ok()

    monkeypatch.setattr(pipeline, "_run_cocoindex_update_impl", fake_impl)
    monkeypatch.setattr(pipeline, "_maybe_run_serialized_optimize", lambda *a, **k: None)


def test_full_reprocess_drops_tables_first(monkeypatch) -> None:
    """full_reprocess=True drops exactly once before the update (INSERT path)."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    drops: list[dict] = []

    def fake_drop(env, *, quiet):
        drops.append(env)
        return _ok()

    monkeypatch.setattr(pipeline, "run_cocoindex_drop", fake_drop)

    pipeline.run_cocoindex_update({"X": "1"}, full_reprocess=True, quiet=True)

    assert len(drops) == 1, "full_reprocess must drop exactly once before update"
    assert drops[0] == {"X": "1"}, "drop must receive the same env as the update"
    assert seen["update"] == 1


def test_increment_does_not_drop(monkeypatch) -> None:
    """full_reprocess=False (increment) must NOT drop — it would lose the table."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    drops: list[dict] = []

    def fake_drop(env, *, quiet):
        drops.append(env)
        return _ok()

    monkeypatch.setattr(pipeline, "run_cocoindex_drop", fake_drop)

    pipeline.run_cocoindex_update({}, full_reprocess=False, quiet=True)

    assert drops == [], "increment must not drop the tables"
    assert seen["update"] == 1


def test_drop_failure_falls_back_to_inplace(monkeypatch, capsys) -> None:
    """A non-preflight drop failure does not abort — the update still runs in-place."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    monkeypatch.setattr(
        pipeline,
        "run_cocoindex_drop",
        lambda env, *, quiet: subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="boom"
        ),
    )

    pipeline.run_cocoindex_update({}, full_reprocess=True, quiet=True)

    assert seen["update"] == 1, "update must still run after a non-fatal drop failure"
    assert "drop-before-reprocess failed" in capsys.readouterr().err


def test_drop_preflight_blocker_is_silent(monkeypatch, capsys) -> None:
    """A preflight drop stub (cocoindex not installed, e.g. graph-only) is not noisy."""
    seen: dict = {}
    _stub_impl(monkeypatch, seen)
    monkeypatch.setattr(
        pipeline,
        "run_cocoindex_drop",
        lambda env, *, quiet: subprocess.CompletedProcess(
            args=["cocoindex"], returncode=127, stdout="", stderr="not found"
        ),
    )

    pipeline.run_cocoindex_update({}, full_reprocess=True, quiet=True)

    assert seen["update"] == 1
    # 127 preflight is expected on graph-only installs and must NOT warn.
    assert "drop-before-reprocess failed" not in capsys.readouterr().err


def test_popen_captures_normal_child_output() -> None:
    """Regression guard: a completing child's stdout/stderr/code are still captured
    after the wait-before-join reorder (verifies normal operation is unchanged)."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('hello-out'); "
            "sys.stderr.write('hello-err'); sys.exit(0)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        out, err, code = pipeline._popen_capturing_stderr(proc, verbose=True)
    finally:
        proc.wait()
    assert code == 0
    assert out == "hello-out"
    assert "hello-err" in err


def test_popen_abort_terminates_child_without_joining() -> None:
    """Ctrl+C path: when ``proc.wait()`` raises while drain threads are blocked,
    ``_popen_capturing_stderr`` must terminate the child and re-raise WITHOUT
    joining the drain threads — a join here re-introduces the uninterruptible
    hang (``Thread.join()`` on an infinite-timeout lock cannot be interrupted by
    SIGINT), which is exactly the bug being fixed.

    Run the helper in a daemon thread with a hard deadline so that a regression
    (a join sneaking back onto the abort path) fails the assertion instead of
    hanging the whole test session.
    """
    release = threading.Event()

    class _BlockingStream:
        """``proc.stdout``/``.stderr`` stand-in: ``read()`` blocks until released."""

        def read(self, _n: int) -> bytes:
            release.wait()
            return b""

    class _InterruptedProc:
        def __init__(self) -> None:
            self.stdout = _BlockingStream()
            self.stderr = _BlockingStream()
            self.terminated = False

        def wait(self, *_a, **_k) -> int:
            # Simulate Ctrl+C interrupting the (interruptible) wait on the child.
            raise KeyboardInterrupt

        def terminate(self) -> None:
            self.terminated = True

    proc = _InterruptedProc()
    outcome: dict = {}

    def runner() -> None:
        try:
            pipeline._popen_capturing_stderr(proc, verbose=True)
            outcome["raised"] = None
        except BaseException as exc:  # noqa: BLE001 — we WANT every escape
            outcome["raised"] = exc
        finally:
            release.set()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=5.0)
    # Snapshot liveness BEFORE releasing: with the buggy join-first order the
    # runner would still be blocked in Thread.join() here (deterministic); with
    # the fix it returns in milliseconds. Checking after release.set() would race.
    hung = t.is_alive()
    release.set()  # never leave drain threads blocked, even on a passing run
    t.join(timeout=5.0)

    assert not hung, (
        "_popen_capturing_stderr hung on the abort path — it must not join the "
        "drain threads when the child wait is interrupted (Ctrl+C regression)"
    )
    assert isinstance(outcome.get("raised"), KeyboardInterrupt), (
        "expected the KeyboardInterrupt to propagate out of the abort path"
    )
    assert proc.terminated, "the spawned child must be torn down on abort"
