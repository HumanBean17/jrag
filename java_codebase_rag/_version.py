"""Version string for the CLI ``--version`` flag.

The single source of truth is the installed distribution metadata
(``java-codebase-rag`` in pyproject.toml), read via :mod:`importlib.metadata`
so a pyproject bump propagates with no second hardcoded copy.
:func:`version_string` appends the CPython version for the
``<prog> <version> (python <x.y.z>)`` format chosen for the ``--version`` flag.

Stdlib-only on purpose: this is imported at module load by both CLIs, and
``jrag`` keeps ``build_parser()`` free of torch / sentence_transformers / mcp_v2.
"""
from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

_PACKAGE = "java-codebase-rag"


def package_version() -> str:
    """Installed distribution version, or ``"unknown"`` if metadata is absent.

    Absent only when run from a raw checkout without ``pip install -e``; the
    test suite (``conftest.py``) enforces editable install, so this is defensive.
    """
    try:
        return _dist_version(_PACKAGE)
    except PackageNotFoundError:  # pragma: no cover - defensive
        return "unknown"


def version_string(prog: str) -> str:
    """Formatted ``--version`` output: ``<prog> <version> (python <x.y.z>)``."""
    return f"{prog} {package_version()} (python {platform.python_version()})"
