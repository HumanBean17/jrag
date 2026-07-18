"""``--version`` flag for the ``java-codebase-rag`` and ``jrag`` CLIs.

Format contract: ``<prog> <version> (python <x.y.z>)``, exit 0. The version is
read from installed metadata, so assertions match :func:`version_string` rather
than a hardcoded literal (it bumps every release).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from java_codebase_rag import cli, jrag
from java_codebase_rag._version import package_version, version_string

_PY_VER_RE = re.compile(r"\(python \d+\.\d+\.\d+\)")

_ROOT_PYPROJECT = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"


def _root_version() -> str:
    """Read the canonical dist version from the root pyproject."""
    with _ROOT_PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_java_codebase_rag_version_flag(capsys):
    rc = cli.main(["--version"])

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == version_string("java-codebase-rag")
    assert _PY_VER_RE.search(out)


def test_jrag_version_flag(capsys):
    rc = jrag.main(["--version"])

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == version_string("jrag")
    assert _PY_VER_RE.search(out)


def test_version_flag_rejected_after_subcommand():
    """Top-level only: ``jrag <cmd> --version`` is a usage error, not a version print."""
    rc = jrag.main(["status", "--version"])

    assert rc != 0


def test_version_matches_root_pyproject():
    """The dist lookup must resolve to the canonical ``jrag-cli`` metadata.

    Strong, bump-resilient: reads the root pyproject version directly and pins
    ``package_version()`` to it, so a release bump propagates to ``--version``
    without test edits. The ``!= "unknown"`` guard documents the intent (a
    missing/stale dist lookup yields ``"unknown"``); after the rename, the
    legacy ``java-codebase-rag`` dist that lingers in the venv at the pre-rename
    version would also fail this equality.
    """
    # Two guards: not "unknown" documents the missing-dist failure mode; equality
    # with the root pyproject version is the strong, bump-resilient pin.
    assert package_version() != "unknown"
    assert package_version() == _root_version()
