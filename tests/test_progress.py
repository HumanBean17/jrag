"""Unit tests for java_codebase_rag.progress (JCIRAG_PROGRESS protocol).

All tests are LIGHT: no subprocess, no cocoindex, no torch. They exercise the
parser, the renderer (against a non-TTY Console over io.StringIO), the
non-TTY concise-line fallback, and the relay's byte-buffering line split.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

from rich.console import Console

from java_codebase_rag.progress import (
    IndexProgressRenderer,
    ProgressEvent,
    ProgressRelay,
    parse_progress_line,
)

_PREFIX = "JCIRAG_PROGRESS"


# ---------------------------------------------------------------------------
# parse_progress_line
# ---------------------------------------------------------------------------


def test_parse_progress_line_vectors_running() -> None:
    line = f"{_PREFIX} kind=vectors phase=embed done=842 total=1240\n".encode()
    ev = parse_progress_line(line)
    assert ev is not None
    assert ev.kind == "vectors"
    assert ev.phase == "embed"
    assert ev.pass_ is None
    assert ev.done == 842
    assert ev.total == 1240
    assert ev.status == "running"  # default
    assert ev.elapsed_s is None


def test_parse_progress_line_graph_pass() -> None:
    line = f"{_PREFIX} kind=graph phase=build pass=3/6 done=120 total=600\n".encode()
    ev = parse_progress_line(line)
    assert ev is not None
    assert ev.kind == "graph"
    assert ev.phase == "build"
    assert ev.pass_ == "3/6"
    assert ev.done == 120
    assert ev.total == 600


def test_parse_progress_line_optimize_running() -> None:
    line = f"{_PREFIX} kind=optimize phase=compact done=3 total=12\n".encode()
    ev = parse_progress_line(line)
    assert ev is not None
    assert ev.kind == "optimize"
    assert ev.phase == "compact"
    assert ev.done == 3
    assert ev.total == 12


def test_parse_progress_line_done_with_elapsed() -> None:
    line = f"{_PREFIX} kind=vectors status=done elapsed_s=42.1 total=1240\n".encode()
    ev = parse_progress_line(line)
    assert ev is not None
    assert ev.status == "done"
    assert ev.elapsed_s == 42.1
    assert ev.total == 1240


def test_parse_progress_line_non_progress_returns_none() -> None:
    # A cocoindex/lance line that is NOT progress: must return None.
    assert parse_progress_line(b"lance:: reading fragment\n") is None
    assert parse_progress_line(b"some random stderr noise\n") is None


def test_parse_progress_line_malformed_returns_none() -> None:
    # Prefix present but nothing usable after it.
    assert parse_progress_line(f"{_PREFIX}\n".encode()) is None
    assert parse_progress_line(f"{_PREFIX}   \n".encode()) is None
    # Must never raise, even on garbage.
    assert parse_progress_line(b"") is None
    assert parse_progress_line(f"{_PREFIX} = = =\n".encode()) is None


# ---------------------------------------------------------------------------
# ProgressRelay
# ---------------------------------------------------------------------------


@dataclass
class _ApplyRecord:
    kind: str
    phase: str | None
    pass_: str | None
    done: int | None
    total: int | None
    status: Literal["running", "done", "failed"]
    elapsed_s: float | None


class _StubRenderer:
    """Captures apply() calls; never prints. Used to test the relay in isolation."""

    def __init__(self) -> None:
        self.applied: list[ProgressEvent] = []

    def apply(self, ev: ProgressEvent) -> None:
        self.applied.append(ev)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_progress_relay_parses_split_chunk_once() -> None:
    """One logical progress line fed as two feed() calls across a chunk boundary
    results in exactly ONE renderer.apply() call, and the raw line is NOT printed."""
    stub = _StubRenderer()
    buf = io.BytesIO()
    relay = ProgressRelay(renderer=stub, console=Console(file=buf, force_terminal=False))
    full = f"{_PREFIX} kind=vectors done=5 total=10\n".encode()
    mid = len(full) // 2
    relay.feed(full[:mid])
    relay.feed(full[mid:])
    assert len(stub.applied) == 1
    ev = stub.applied[0]
    assert ev.kind == "vectors"
    assert ev.done == 5
    assert ev.total == 10
    # The progress line must be consumed/suppressed, not echoed to the console.
    assert buf.getvalue() == b""


def test_progress_relay_relays_non_progress_line() -> None:
    """A non-progress, non-noise line reaches the output sink (console)."""
    stub = _StubRenderer()
    buf = io.StringIO()
    relay = ProgressRelay(
        renderer=stub, console=Console(file=buf, force_terminal=False, force_interactive=False)
    )
    relay.feed(b"cocoindex: importing flow\n")
    out = buf.getvalue()
    assert "cocoindex: importing flow" in out
    assert len(stub.applied) == 0


def test_progress_relay_suppresses_noise_continuation() -> None:
    """A noise header (``FutureWarning:``) plus its indented traceback frame are
    BOTH suppressed; a following normal line is still emitted. Mirrors
    ``_LineFilter``/``_AsyncLineFilter``'s ``_suppress_next`` behavior."""
    stub = _StubRenderer()
    buf = io.StringIO()
    relay = ProgressRelay(
        renderer=stub, console=Console(file=buf, force_terminal=False, force_interactive=False)
    )
    relay.feed(b"/some/conda/env.py:1: FutureWarning: something deprecated\n")
    relay.feed(b"    some/frame.py:42: DeprecationWarning\n")
    relay.feed(b"cocoindex: indexing batch\n")
    out = buf.getvalue()
    # The noise header and its indented continuation must NOT reach the sink.
    assert "FutureWarning" not in out
    assert "some/frame.py:42" not in out
    # The normal line that follows must still be emitted.
    assert "cocoindex: indexing batch" in out
    # No progress events were parsed.
    assert len(stub.applied) == 0


# ---------------------------------------------------------------------------
# IndexProgressRenderer — TTY path (against a forced-terminal Console)
# ---------------------------------------------------------------------------


def test_renderer_task_pending_until_first_event() -> None:
    buf = io.StringIO()
    # Force a terminal so the Live path is taken (not the concise fallback).
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    r = IndexProgressRenderer(phases=["vectors", "graph", "optimize"], console=console)
    r.start()
    try:
        # No event yet: every task must be invisible / not started (pending).
        for task in r._progress.tasks:
            assert not task.visible
            assert not task.started
    finally:
        r.stop()


def test_renderer_clamps_completed_to_total_on_done() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    r = IndexProgressRenderer(phases=["vectors"], console=console)
    r.start()
    try:
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=80, total=100, status="running", elapsed_s=None
            )
        )
        # While running, completed tracks the event's done exactly.
        assert r._progress.tasks[r._task_ids["vectors"]].completed == 80
        # Terminal event with status=done and no new done value: clamp to total.
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=None, total=100, status="done", elapsed_s=1.0
            )
        )
        assert r._progress.tasks[r._task_ids["vectors"]].completed == 100
        assert r._progress.tasks[r._task_ids["vectors"]].finished
    finally:
        r.stop()


def test_renderer_indeterminate_total_none() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    r = IndexProgressRenderer(phases=["graph"], console=console)
    r.start()
    try:
        # Events with no total → task stays indeterminate (total is None).
        r.apply(
            ProgressEvent(
                kind="graph", phase="build", pass_=None, done=5, total=None, status="running", elapsed_s=None
            )
        )
        task = r._progress.tasks[r._task_ids["graph"]]
        assert task.total is None
    finally:
        r.stop()


def test_renderer_failed_marks_task_red() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    r = IndexProgressRenderer(phases=["vectors"], console=console)
    r.start()
    try:
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=2, total=10, status="running", elapsed_s=None
            )
        )
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=2, total=10, status="failed", elapsed_s=None
            )
        )
        task = r._progress.tasks[r._task_ids["vectors"]]
        # The task is stopped (spinner halted) — rich records a stop_time — and the
        # description carries the red ✗ marker. ``started`` stays True (start is
        # irreversible); ``stop_time`` is the authoritative "halted" signal.
        assert task.stop_time is not None
        assert "✗" in task.description
    finally:
        r.stop()


# ---------------------------------------------------------------------------
# IndexProgressRenderer — non-TTY concise-line fallback
# ---------------------------------------------------------------------------


def test_non_tty_fallback_emits_concise_lines() -> None:
    """Non-TTY: concise lines appear, throttled to ~5s/phase, plus a terminal line."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = IndexProgressRenderer(phases=["vectors"], console=console)
    assert r._fallback is True
    r.start()
    t0 = r._now()
    try:
        # First event: a concise progress line appears immediately.
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=842, total=1240, status="running", elapsed_s=None
            )
        )
        first = buf.getvalue()
        assert "vectors" in first
        assert "842" in first and "1240" in first
        # A second running event within the throttle window is suppressed.
        n_before = len(buf.getvalue())
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=900, total=1240, status="running", elapsed_s=None
            )
        )
        # Throttle: no new line within the window (monotonic clock hasn't advanced).
        assert len(buf.getvalue()) == n_before
        # Push the throttle window past ~5s: next running event prints again.
        r._last_print_at["vectors"] = t0 - 10.0
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=1000, total=1240, status="running", elapsed_s=None
            )
        )
        assert "1000" in buf.getvalue()
        # Terminal event: always prints (done line), regardless of throttle.
        r.apply(
            ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=1240, total=1240, status="done", elapsed_s=42.1
            )
        )
        final = buf.getvalue()
        assert "done" in final
        assert "42.1" in final
    finally:
        r.stop()
