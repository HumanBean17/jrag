"""Package-shape tests for the ``java-codebase-rag`` PyPI shim.

The rename (Task 7 of the jrag rename; canonical dist → ``jrag-cli``) keeps
the old name ``java-codebase-rag`` alive on PyPI as a metadata-only shim:
zero modules, zero console scripts, ``requires-dist`` of exactly
``jrag-cli==<lockstep-version>``. Existing users running
``pip install -U java-codebase-rag`` transitively pull ``jrag-cli``, which
provides every console script (``jrag``, ``jrag-mcp``, and the legacy
``java-codebase-rag`` / ``java-codebase-rag-mcp`` aliases).

These tests pin the shim's ``shim/pyproject.toml`` shape so a future edit
can't silently:
  - rename the shim,
  - drift its version out of lockstep with the canonical dist,
  - add a ``[project.scripts]`` (would shadow the canonical dist's scripts
    during the very upgrade the shim exists to smooth), or
  - declare package discovery (the shim ships no importable modules).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHIM_PYPROJECT = REPO_ROOT / "shim" / "pyproject.toml"
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def test_shim_pyproject_exists() -> None:
    """Guard: the shim config lives at ``shim/pyproject.toml`` (not repo root)."""
    assert SHIM_PYPROJECT.is_file(), f"shim pyproject missing: {SHIM_PYPROJECT}"


def test_shim_name_is_legacy() -> None:
    data = _load(SHIM_PYPROJECT)
    assert data["project"]["name"] == "java-codebase-rag"


def test_shim_version_matches_brief() -> None:
    data = _load(SHIM_PYPROJECT)
    assert data["project"]["version"] == "0.12.0"


def test_shim_depends_only_on_jrag_cli() -> None:
    """The shim's sole runtime dep is the canonical dist, pinned exactly."""
    data = _load(SHIM_PYPROJECT)
    assert data["project"]["dependencies"] == ["jrag-cli==0.12.0"]


def test_shim_declares_no_console_scripts() -> None:
    """A ``[project.scripts]`` here would collide with ``jrag-cli`` on upgrade."""
    data = _load(SHIM_PYPROJECT)
    assert "scripts" not in data["project"], (
        "shim must not declare [project.scripts] — the canonical jrag-cli dist "
        "owns every console script"
    )


def test_shim_declares_no_package_discovery() -> None:
    """The shim ships no importable modules, so setuptools must not discover any.

    Covers both ``[tool.setuptools.packages]`` (explicit list) and
    ``[tool.setuptools.packages.find]`` (auto-discovery). Either would make
    setuptools expect a module tree the shim doesn't have.
    """
    data = _load(SHIM_PYPROJECT)
    tool_setuptools = data.get("tool", {}).get("setuptools", {})
    assert "packages" not in tool_setuptools, (
        "shim must not declare [tool.setuptools.packages] — it has no modules"
    )
    assert "find" not in tool_setuptools.get("packages", {}), (
        "shim must not declare [tool.setuptools.packages.find] — it has no modules"
    )


def test_shim_version_lockstep_with_root() -> None:
    """Shim version must equal the canonical dist version (root pyproject)."""
    shim = _load(SHIM_PYPROJECT)
    root = _load(ROOT_PYPROJECT)
    assert shim["project"]["version"] == root["project"]["version"], (
        f"shim version {shim['project']['version']!r} drifted from root "
        f"{root['project']['version']!r} — bump both in lockstep"
    )
