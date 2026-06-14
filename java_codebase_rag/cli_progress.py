"""CLI-owned stderr progress lines (shared by server reprocess path and pipeline helpers)."""
from __future__ import annotations

import asyncio
import sys
from typing import Callable

from java_codebase_rag.cli_format import bold_cyan, is_noise_line, styled_check, styled_cross
from java_codebase_rag.progress import ProgressEvent, make_relay


def emit_vectors_start() -> None:
    print(
        bold_cyan("[vectors]") + " running · cocoindex update",
        file=sys.stderr,
        flush=True,
    )


def emit_vectors_finish(*, elapsed_s: float, exit_code: int) -> None:
    marker = styled_check() if exit_code == 0 else styled_cross()
    print(
        f"{marker} {bold_cyan('[vectors]')} finished · {elapsed_s:.2f}s"
        + (f" (exit={exit_code})" if exit_code != 0 else ""),
        file=sys.stderr,
        flush=True,
    )


class _AsyncLineFilter:
    """Buffers byte chunks and relays only non-noise lines to stderr (async drain path)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._suppress_next = False

    def feed(self, chunk: bytes) -> None:
        self._buf.extend(chunk)
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line += b"\n"
            noise = is_noise_line(line)
            if noise:
                self._suppress_next = True
                continue
            if self._suppress_next and line[:1] in (b" ", b"\t"):
                continue
            self._suppress_next = False
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()

    def flush(self) -> None:
        if self._buf:
            if not is_noise_line(self._buf):
                sys.stderr.buffer.write(bytes(self._buf))
                sys.stderr.buffer.flush()
            self._buf.clear()
        self._suppress_next = False


async def accumulate_and_relay_subprocess_streams(
    proc: asyncio.subprocess.Process,
    *,
    relay: bool,
    verbose: bool = True,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_progress_console: object | None = None,
) -> tuple[bytes, bytes]:
    """Read stdout and stderr until EOF; optionally copy non-noise stderr chunks to stderr.

    When ``on_progress`` is set, stderr is drained through a :class:`ProgressRelay`
    so ``JCIRAG_PROGRESS`` lines are parsed and routed to ``on_progress`` (and
    suppressed from the relay), matching the sync ``pipeline._popen_capturing_stderr``.
    """
    stdout = proc.stdout
    stderr = proc.stderr
    if stdout is None or stderr is None:
        raise RuntimeError("subprocess must be created with stdout=PIPE and stderr=PIPE")

    out_buf = bytearray()
    err_buf = bytearray()
    if on_progress is not None:
        filt = make_relay(on_progress, console=on_progress_console, verbose=verbose)
    elif relay and not verbose:
        filt = _AsyncLineFilter()
    else:
        filt = None

    async def drain_stdout(reader: asyncio.StreamReader, target: bytearray) -> None:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            target.extend(chunk)

    async def drain_stderr(reader: asyncio.StreamReader, target: bytearray) -> None:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            target.extend(chunk)
            if filt is not None:
                filt.feed(chunk)
            elif relay:
                sys.stderr.buffer.write(chunk)
                sys.stderr.buffer.flush()

    await asyncio.gather(drain_stdout(stdout, out_buf), drain_stderr(stderr, err_buf))
    await proc.wait()
    if filt is not None:
        filt.flush()
    return bytes(out_buf), bytes(err_buf)
