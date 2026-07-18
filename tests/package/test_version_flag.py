"""``--version`` flag for the ``java-codebase-rag`` and ``jrag`` CLIs.

Format contract: ``<prog> <version> (python <x.y.z>)``, exit 0. The version is
read from installed metadata, so assertions match :func:`version_string` rather
than a hardcoded literal (it bumps every release).
"""
from __future__ import annotations

import re

from java_codebase_rag import cli, jrag
from java_codebase_rag._version import package_version, version_string

_PY_VER_RE = re.compile(r"\(python \d+\.\d+\.\d+\)")


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


def test_version_is_not_unknown():
    """The dist lookup must resolve to the installed ``jrag-cli`` metadata, not ``unknown``.

    Pins that ``_PACKAGE`` names the canonical post-rename distribution (``jrag-cli``)
    so a pyproject bump propagates to ``--version``. After the rename, the legacy
    ``java-codebase-rag`` dist lingers in the venv at the pre-rename version; if
    ``_PACKAGE`` still pointed there, this assertion would catch the stale read.
    """
    # Two guards: not "unknown" catches a missing/stale dist lookup; not "0.11.2"
    # catches a regression to the pre-rename distribution that still lingers here.
    assert package_version() != "unknown"
    assert package_version() != "0.11.2"
