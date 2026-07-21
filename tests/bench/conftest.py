"""Make the standalone ``bench`` package importable under pytest (src-layout).

``bench/`` lives at the repo root as a sibling of ``src/`` and is deliberately
NOT part of the ``java_codebase_rag`` distribution, so it is not on any
installed path. Insert the repo root so ``import bench.<module>`` resolves.

NOTE: ``tests/bench/`` deliberately has NO ``__init__.py``. ``pytest.ini`` sets
``pythonpath = src tests``; if ``tests/bench/__init__.py`` existed, the regular
package ``bench`` would resolve to the *test* dir and shadow the source package.
Without it, ``tests/bench`` is only a namespace candidate and the regular
``bench/`` package at the repo root wins. Do not re-add ``__init__.py`` here.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _find_jqassistant() -> str | None:
    """Resolve the jqassistant CLI binary, or None if not installed."""
    env = __import__("os").environ.get("JQASSISTANT_BIN")
    if env and Path(env).is_file():
        return env
    hits = sorted(Path.home().glob("jqassistant-cli/*/bin/jqassistant"))
    if hits:
        return str(hits[0])
    return shutil.which("jqassistant")


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_jqa: needs the jqassistant CLI + a JDK; skipped if absent.",
    )
    config.addinivalue_line(
        "markers",
        "requires_jdk: needs javac/jdeps on PATH; skipped if absent.",
    )
    config.addinivalue_line(
        "markers",
        "requires_claude: needs the claude CLI on PATH; skipped if absent.",
    )


def pytest_collection_modifyitems(config, items) -> None:
    jqa = _find_jqassistant()
    jdk = shutil.which("javac") and shutil.which("jdeps")
    claude = shutil.which("claude")
    skip_jqa = pytest.mark.skip(reason="jqassistant CLI not found (set JQASSISTANT_BIN)")
    skip_jdk = pytest.mark.skip(reason="javac/jdeps not found on PATH")
    skip_claude = pytest.mark.skip(reason="claude CLI not found on PATH")
    for item in items:
        if "requires_jqa" in item.keywords and not jqa:
            item.add_marker(skip_jqa)
        if "requires_jdk" in item.keywords and not jdk:
            item.add_marker(skip_jdk)
        if "requires_claude" in item.keywords and not claude:
            item.add_marker(skip_claude)


@pytest.fixture(scope="session")
def jqassistant_bin() -> str:
    bin_path = _find_jqassistant()
    if not bin_path:
        pytest.skip("jqassistant CLI not found (set JQASSISTANT_BIN)")
    return bin_path

