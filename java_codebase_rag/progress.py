"""JCIRAG_PROGRESS protocol: parse, render, and relay subprocess progress.

The five lifecycle commands (``init`` / ``increment`` / ``install`` /
``reprocess`` / ``update``) drive two subprocesses — ``cocoindex update``
(vectors → Lance) and ``build_ast_graph.py`` (graph → LadybugDB) — whose
stderr output is inconsistent and shows no real progress. This module is the
foundation of a unified progress surface.

A subprocess prints lines of the form::

    JCIRAG_PROGRESS kind=vectors phase=embed done=842 total=1240
    JCIRAG_PROGRESS kind=graph phase=build pass=3/6 done=120 total=600 status=running
    JCIRAG_PROGRESS kind=optimize status=done elapsed_s=42.1 total=1240

to its **stderr**. The parent (``pipeline._LineFilter`` /
``cli_progress._AsyncLineFilter`` drain path) feeds each stderr chunk to a
:class:`ProgressRelay`, which:

1. buffers chunks and splits on ``\\n`` (mirrors the existing line filters),
2. parses each complete line with :func:`parse_progress_line`,
3. if it is a progress line → forwards a :class:`ProgressEvent` to an
   :class:`IndexProgressRenderer` and **suppresses** the raw line, else
4. runs the existing noise matcher (``is_noise_line``) and routes the
   surviving line to the active sink (console / raw stderr / drop).

On a TTY the renderer drives a ``rich.progress.Live`` region; off-TTY it
prints concise throttled lines (one per phase, ~every 5 s, plus a final
terminal line). This module is pure library — no production caller is wired
in yet.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from java_codebase_rag.cli_format import is_noise_line

__all__ = [
    "ProgressEvent",
    "parse_progress_line",
    "IndexProgressRenderer",
    "ProgressRelay",
]

ProgressKind = Literal["vectors", "graph", "optimize"]
ProgressStatus = Literal["running", "done", "failed"]

_PREFIX = "JCIRAG_PROGRESS"
# Non-TTY concise-line throttle window, in seconds, per phase.
_FALLBACK_THROTTLE_S = 5.0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@dataclass
class ProgressEvent:
    """A single parsed ``JCIRAG_PROGRESS`` line.

    ``pass_`` carries a multi-pass label like ``"3/6"`` (graph builder passes).
    All optional fields are ``None`` when the emitting line omits the token.
    """

    kind: ProgressKind
    phase: str | None
    pass_: str | None
    done: int | None
    total: int | None
    status: ProgressStatus
    elapsed_s: float | None


def parse_progress_line(line: bytes) -> ProgressEvent | None:
    """Parse one raw stderr line into a :class:`ProgressEvent`.

    Returns ``None`` (never raises) when the line:

    * does not start (after stripping a leading ``\\r``) with the exact
      ``JCIRAG_PROGRESS`` prefix, or
    * has the prefix but no usable tokens afterwards (malformed).

    The remainder after the prefix is whitespace-separated ``key=value``
    tokens. ``kind`` is required (omitting it → ``None``). ``status`` defaults
    to ``"running"``. Double / extra spaces are tolerated.
    """
    try:
        text = line.decode("utf-8", errors="replace")
    except Exception:
        return None
    # Tolerate a carriage-return rewind before the prefix.
    stripped = text.lstrip("\r")
    if not stripped.startswith(_PREFIX):
        return None
    tail = stripped[len(_PREFIX):]
    # Tokenize on whitespace; empty/whitespace-only tails are malformed.
    tokens = tail.split()
    if not tokens:
        return None

    fields: dict[str, str] = {}
    for tok in tokens:
        if "=" not in tok:
            # A bare token is not a key=value pair → malformed line.
            continue
        key, _, value = tok.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            fields[key] = value

    if not fields:
        return None

    kind_raw = fields.get("kind")
    if kind_raw not in ("vectors", "graph", "optimize"):
        # kind is required and must be one of the known kinds.
        return None

    def _maybe_int(name: str) -> int | None:
        raw = fields.get(name)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _maybe_float(name: str) -> float | None:
        raw = fields.get(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    status_raw = fields.get("status", "running")
    if status_raw not in ("running", "done", "failed"):
        status_raw = "running"

    return ProgressEvent(
        kind=kind_raw,  # type: ignore[arg-type]
        phase=fields.get("phase") or None,
        pass_=fields.get("pass") or None,
        done=_maybe_int("done"),
        total=_maybe_int("total"),
        status=status_raw,  # type: ignore[arg-type]
        elapsed_s=_maybe_float("elapsed_s"),
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def _build_progress(console: Console) -> Progress:
    """Construct the rich.Progress with the canonical column layout.

    The label TextColumn reads ``task.fields["label"]`` so the phase name
    shown in the bar is independent of the (mutable) description, which we
    repurpose for status suffixes (``✗`` on failure).
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.fields[label]}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("{task.description}"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


class IndexProgressRenderer:
    """Owns the rich Progress region (TTY) or the concise-line fallback (non-TTY).

    Construction registers one task per phase name, each ``total=None`` and
    **invisible** (the "never spawned" invariant: no task is ``running`` until
    its first :meth:`apply` arrives). The first event for a kind flips that
    kind's task visible + started.

    On a non-TTY console (``console.is_terminal is False``) no Live region is
    used; instead concise lines are printed via :meth:`_fallback_print`,
    throttled to once per ~5 s per phase, plus one terminal line per phase.
    """

    def __init__(self, phases: list[str], *, console: Console | None = None) -> None:
        self._console: Console = console if console is not None else Console(stderr=True)
        self._phases: list[str] = list(phases)
        self._fallback: bool = not self._console.is_terminal
        self._progress: Progress = _build_progress(self._console)
        # kind -> rich task id. Start every task invisible + not started so the
        # "never spawned" invariant holds until the first event arrives.
        self._task_ids: dict[str, int] = {}
        for phase in self._phases:
            tid = self._progress.add_task(
                phase,
                total=None,
                visible=False,
                start=False,
                label=phase,
            )
            self._task_ids[phase] = tid
        self._live: Live | None = None
        # Non-TTY throttle bookkeeping (monotonic seconds of last concise print).
        self._last_print_at: dict[str, float] = {phase: 0.0 for phase in self._phases}
        # Non-TTY carry-forward: a minimal ``done`` event may omit total /
        # elapsed_s; fall back to the last-seen values so the concise terminal
        # line never degrades (e.g. stays ``vectors done · 1240 · 42.1s``).
        self._last_total: dict[str, int | None] = {phase: None for phase in self._phases}
        self._last_elapsed: dict[str, float | None] = {phase: None for phase in self._phases}
        self._started: bool = False

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Enter the Live region (TTY) or no-op (non-TTY). Idempotent."""
        if self._started:
            return
        self._started = True
        if not self._fallback:
            self._live = Live(
                self._progress,
                console=self._console,
                refresh_per_second=10,
                transient=False,
            )
            self._live.start()

    def stop(self) -> None:
        """Exit the Live region (TTY) or no-op (non-TTY). Safe to call once."""
        if not self._started:
            return
        self._started = False
        if self._live is not None:
            self._live.stop()
            self._live = None

    # -- routing ---------------------------------------------------------

    def apply(self, ev: ProgressEvent) -> None:
        """Route a single :class:`ProgressEvent` to its matching phase task.

        First event for a kind makes that task visible + started. ``total`` and
        ``done`` are applied directly when present. ``done`` is clamped to be
        non-decreasing (producers should emit monotonically non-decreasing
        ``done``; this defensive clamp enforces it so a stray smaller value
        can't rewind the bar). ``status == "done"`` unconditionally clamps
        completed to the total in both directions (an approximate total can't
        stall below 100%, nor can an approximate pre-walk over-count exceed it).
        ``status == "failed"`` halts the task and marks the description with a
        red ``✗`` (rich renders the spinner stopped). On non-TTY consoles this
        delegates to the throttled concise-line printer.
        """
        if self._fallback:
            self._fallback_apply(ev)
            return
        tid = self._task_ids.get(ev.kind)
        if tid is None:
            return
        task = self._progress.tasks[tid]
        # First event: promote from pending to running/visible.
        if not task.started:
            self._progress.start_task(tid)
            self._progress.update(tid, visible=True)
        if ev.total is not None:
            self._progress.update(tid, total=ev.total)
        if ev.done is not None:
            # Set-based (not advance-based) for determinism: each event carries
            # the absolute completed count, not a delta. Monotonic clamp: never
            # let a smaller done rewind the bar.
            new_completed = max(task.completed, ev.done)
            self._progress.update(tid, completed=new_completed)
        if ev.status == "done":
            # Two-way clamp: completed must equal total on done. An approximate
            # total can under-count (stall below 100%) or the propose's
            # approximate pre-walk can over-count; both resolve to == total.
            if task.total is not None and task.completed != task.total:
                self._progress.update(tid, completed=task.total)
            self._progress.update(tid, description=f"{ev.kind} ✓")
            self._progress.stop_task(tid)
        elif ev.status == "failed":
            self._progress.update(tid, description=f"{ev.kind} ✗")
            self._progress.stop_task(tid)

    # -- non-TTY fallback ------------------------------------------------

    def _now(self) -> float:
        """Indirection for tests; returns the monotonic clock in seconds."""
        return time.monotonic()

    def _fallback_apply(self, ev: ProgressEvent) -> None:
        """Concise-line path for non-TTY consoles."""
        last = self._last_print_at.get(ev.kind, 0.0)
        now = self._now()
        terminal = ev.status in ("done", "failed")
        throttle_ok = (now - last) >= _FALLBACK_THROTTLE_S
        if not terminal and not throttle_ok and last != 0.0:
            # Suppressed by the throttle window. (``last != 0.0`` lets the very
            # first event for a phase print immediately.) Still track totals so
            # a later terminal line can carry them forward.
            if ev.total is not None:
                self._last_total[ev.kind] = ev.total
            if ev.elapsed_s is not None:
                self._last_elapsed[ev.kind] = ev.elapsed_s
            return
        # Carry forward last-seen total / elapsed_s so a minimal ``done`` event
        # that omits them still prints a complete terminal line.
        carried_total = ev.total
        if carried_total is None:
            carried_total = self._last_total.get(ev.kind)
        carried_elapsed = ev.elapsed_s
        if carried_elapsed is None:
            carried_elapsed = self._last_elapsed.get(ev.kind)
        # Update the carry-forward state with whatever this event carried.
        if ev.total is not None:
            self._last_total[ev.kind] = ev.total
        if ev.elapsed_s is not None:
            self._last_elapsed[ev.kind] = ev.elapsed_s
        self._last_print_at[ev.kind] = now
        self._console.print(
            self._format_concise(ev, total=carried_total, elapsed_s=carried_elapsed)
        )

    def _format_concise(
        self,
        ev: ProgressEvent,
        *,
        total: int | None = None,
        elapsed_s: float | None = None,
    ) -> Text:
        """Render one concise line for the non-TTY fallback.

        ``total`` / ``elapsed_s`` are the already-carry-forwarded values to show
        (the caller resolves event-vs-last-seen before formatting); ``ev``'s own
        fields are only used for status / done / phase.
        """
        kind = ev.kind
        if ev.status == "done":
            total_str = str(total) if total is not None else ""
            elapsed = f"{elapsed_s:.1f}s" if elapsed_s is not None else ""
            bits = [kind, "done"]
            if total_str != "":
                bits.append(total_str)
            if elapsed:
                bits.append(elapsed)
            return Text(" · ".join(bits))
        if ev.status == "failed":
            return Text(f"{kind} failed")
        if ev.done is not None and ev.total is not None and ev.total > 0:
            pct = int(round(ev.done * 100.0 / ev.total))
            return Text(f"{kind} {ev.done}/{ev.total} ({pct}%)")
        if ev.done is not None:
            return Text(f"{kind} {ev.done}")
        return Text(kind)


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------


class ProgressRelay:
    """Single-writer bridge between a subprocess stderr drain and the renderer.

    Mirrors the byte-buffering of ``cli_progress._AsyncLineFilter`` /
    ``pipeline._LineFilter``: accumulate chunks, split on ``\\n``, route each
    complete line. When ``verbose`` is True (and no renderer is attached) the
    relay writes raw bytes to ``sys.stderr.buffer`` (raw mode, no Live region).
    """

    def __init__(
        self,
        renderer: IndexProgressRenderer | None,
        *,
        console: Console | None = None,
        verbose: bool = False,
    ) -> None:
        self._renderer = renderer
        self._verbose = verbose
        self._console: Console | None = console
        self._buf = bytearray()
        # Live region is only meaningful when a renderer is attached.
        self._live_active: bool = renderer is not None
        # Mirrors ``_LineFilter._suppress_next``: a noise header line (e.g. a
        # ``FutureWarning:`` banner) suppresses the NEXT line too, which is its
        # indented traceback frame(s). ``line[:1] in (b" ", b"\t")`` is the
        # continuation signal. Progress lines reset this (they are consumed by
        # the renderer, never noise).
        self._suppress_next: bool = False

    def feed(self, chunk: bytes) -> None:
        """Buffer ``chunk`` and route each complete (``\\n``-terminated) line."""
        self._buf.extend(chunk)
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line += b"\n"
            self._route_line(line)

    def flush(self) -> None:
        """Emit any trailing partial buffer (without a trailing newline)."""
        if self._buf:
            # Trailing partial line: continuation suppression does not apply
            # (there is no following line). Route it directly.
            self._route_line(bytes(self._buf))
            self._buf.clear()

    def _route_line(self, line: bytes) -> None:
        ev = parse_progress_line(line)
        if ev is not None and self._renderer is not None:
            # Consumed by the protocol — never echoed to any sink. It is not
            # noise, so it must not keep the suppression flag armed.
            self._suppress_next = False
            self._renderer.apply(ev)
            return
        if ev is not None and self._renderer is None:
            # Parsed as progress but no renderer attached: still reset the flag
            # (a progress line is never noise) and drop quietly.
            self._suppress_next = False
            return
        # Non-progress line: noise path, with continuation suppression.
        if is_noise_line(line):
            self._suppress_next = True
            return
        if self._suppress_next and line[:1] in (b" ", b"\t"):
            # Indented continuation of the preceding noise header (e.g. a
            # traceback frame). Drop without disarming: a multi-frame traceback
            # has several such lines in a row.
            return
        self._suppress_next = False
        text = line.decode("utf-8", errors="replace")
        if self._renderer is not None and self._live_active:
            console = self._console if self._console is not None else self._renderer._console  # noqa: SLF001
            # rich.Console over a Live region must suspend/resume to interleave
            # a one-off line without corrupting the bar redraw; print() handles
            # this correctly when the Live was started on the same console.
            console.print(text, end="")
            return
        if self._verbose and self._renderer is None:
            try:
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()
            except Exception:
                pass
            return
        # Neither verbose nor a renderer: drop quietly (quiet mode).
