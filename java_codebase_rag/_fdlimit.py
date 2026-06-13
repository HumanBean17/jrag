"""Raise the process soft file-descriptor limit to avoid LanceDB EMFILE.

LanceDB's merge-insert path opens many file handles concurrently; under the
default OS soft ``RLIMIT_NOFILE`` (256 on macOS processes launched by GUI /
launchd / IDE hosts, *not* the shell's raised limit) this exhausts file
descriptors and surfaces as::

    RuntimeError: lance error: LanceError(IO): ... Too many open files (os error 24)
        lance-io-4.0.0/src/local.rs:133:24

``raise_fd_limit`` raises the process's *own* soft limit toward its hard limit.
``RLIMIT_NOFILE`` is inherited across ``fork``+``exec``, so every CocoIndex /
``cocoindex-code`` child spawned afterwards inherits the headroom. This fixes the
failure regardless of launch context (shell vs IDE vs MCP host) and regardless of
Lance's internal IO concurrency.

Never raise to ``RLIM_INFINITY`` — that breaks ``select()``/kqueue and Python
selectors on macOS; ``cap`` bounds the target to a safe value.

See https://github.com/HumanBean17/java-codebase-rag/issues/306
"""

from __future__ import annotations

import resource

# Safe ceiling well above LanceDB's appetite, comfortably below macOS libc
# quirks. The hard limit caps it further if lower (locked-down servers).
_DEFAULT_CAP = 65536


def raise_fd_limit(cap: int = _DEFAULT_CAP) -> None:
    """Raise this process's soft ``RLIMIT_NOFILE`` toward its hard limit.

    Best-effort and silent: never raises. No-op where ``RLIMIT_NOFILE`` is
    unsupported (Windows) or where the soft limit already meets ``min(hard, cap)``.
    """
    if not hasattr(resource, "RLIMIT_NOFILE"):
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(hard, cap)
    if soft >= target:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        # Best-effort: a locked-down environment shouldn't fail the run.
        pass
