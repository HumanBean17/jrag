"""CLI-owned stderr progress lines (shared by server reprocess path and pipeline helpers)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def emit_lance_cocoindex_start(project_root: Path) -> None:
    root = project_root.expanduser().resolve()
    print(
        f"[lance] running cocoindex update (project_root={root})",
        file=sys.stderr,
        flush=True,
    )


def emit_lance_cocoindex_finish(*, elapsed_s: float, exit_code: int) -> None:
    print(
        f"[lance] cocoindex update finished in {elapsed_s:.2f}s (exit={exit_code})",
        file=sys.stderr,
        flush=True,
    )


async def accumulate_and_relay_subprocess_streams(
    proc: asyncio.subprocess.Process,
    *,
    relay: bool,
) -> tuple[bytes, bytes]:
    """Read stdout and stderr until EOF; optionally copy each chunk verbatim to stderr."""
    stdout = proc.stdout
    stderr = proc.stderr
    if stdout is None or stderr is None:
        raise RuntimeError("subprocess must be created with stdout=PIPE and stderr=PIPE")

    out_buf = bytearray()
    err_buf = bytearray()

    async def drain(reader: asyncio.StreamReader, target: bytearray) -> None:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            target.extend(chunk)
            if relay:
                sys.stderr.buffer.write(chunk)
                sys.stderr.buffer.flush()

    await asyncio.gather(drain(stdout, out_buf), drain(stderr, err_buf))
    await proc.wait()
    return bytes(out_buf), bytes(err_buf)
