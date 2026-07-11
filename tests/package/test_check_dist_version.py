"""Tests for the pre-upload dist/ version guard (``scripts/check_dist_version.py``).

The guard is the safety net for ``twine upload dist/*``. These tests pin the
exact failure modes it must catch — most importantly the incident it was
written to prevent: a stale artifact from a prior build sitting next to the
new one in ``dist/``.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_dist_version.py"


def _write_pyproject(path: Path, version: str) -> None:
    path.write_text(
        f"[project]\nname = \"java-codebase-rag\"\nversion = \"{version}\"\n",
        encoding="utf-8",
    )


def _write_wheel(path: Path, version: str) -> None:
    """Build a minimal valid-ish wheel: a zip with a *.dist-info/METADATA entry."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"java_codebase_rag-{version}.dist-info/METADATA", f"Version: {version}\n")


def _run(dist: Path, pyproject: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dist", str(dist), "--pyproject", str(pyproject)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_clean_dist_passes(tmp_path: Path) -> None:
    """Happy path: one wheel + one sdist, both matching pyproject → exit 0."""
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(dist / "java_codebase_rag-0.10.0-py3-none-any.whl", "0.10.0")
    (dist / "java_codebase_rag-0.10.0.tar.gz").write_bytes(b"")
    _write_pyproject(tmp_path / "pyproject.toml", "0.10.0")

    result = _run(dist, tmp_path / "pyproject.toml")

    assert result.returncode == 0, result.stderr
    assert "0.10.0" in result.stdout


def test_empty_dist_fails(tmp_path: Path) -> None:
    """No build ran → refuse, exit 1."""
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_pyproject(tmp_path / "pyproject.toml", "0.10.0")

    result = _run(dist, tmp_path / "pyproject.toml")

    assert result.returncode == 1
    assert "empty" in result.stderr.lower()


def test_stale_foreign_version_is_caught(tmp_path: Path) -> None:
    """The incident scenario: a leftover 0.9.7 artifact next to the new 0.10.0.

    Cleanup failed silently, so dist/ holds both versions. The guard must catch
    the foreign filename and refuse to upload.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(dist / "java_codebase_rag-0.10.0-py3-none-any.whl", "0.10.0")
    (dist / "java_codebase_rag-0.10.0.tar.gz").write_bytes(b"")
    # Stale leftovers from a prior build that cleanup failed to remove.
    _write_wheel(dist / "java_codebase_rag-0.9.7-py3-none-any.whl", "0.9.7")
    (dist / "java_codebase_rag-0.9.7.tar.gz").write_bytes(b"")
    _write_pyproject(tmp_path / "pyproject.toml", "0.10.0")

    result = _run(dist, tmp_path / "pyproject.toml")

    assert result.returncode == 1, (
        f"Guard should reject dist/ with a foreign version.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "0.9.7" in result.stderr
    assert "0.10.0" in result.stderr  # names the target it failed to match


def test_wheel_metadata_mismatch_is_caught(tmp_path: Path) -> None:
    """Filename says 0.10.0 but METADATA says 0.9.7 (forgotten bump) → exit 1."""
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(dist / "java_codebase_rag-0.10.0-py3-none-any.whl", "0.9.7")
    (dist / "java_codebase_rag-0.10.0.tar.gz").write_bytes(b"")
    _write_pyproject(tmp_path / "pyproject.toml", "0.10.0")

    result = _run(dist, tmp_path / "pyproject.toml")

    assert result.returncode == 1
    assert "METADATA" in result.stderr


def test_target_read_from_pyproject_not_args(tmp_path: Path) -> None:
    """No --version flag exists: the target is always pyproject.toml's value.

    If pyproject says 0.10.0 but dist only has 0.9.7, the guard fails — it must
    not trust whatever happens to be in dist/.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(dist / "java_codebase_rag-0.9.7-py3-none-any.whl", "0.9.7")
    (dist / "java_codebase_rag-0.9.7.tar.gz").write_bytes(b"")
    _write_pyproject(tmp_path / "pyproject.toml", "0.10.0")

    result = _run(dist, tmp_path / "pyproject.toml")

    assert result.returncode == 1
    assert "0.10.0" in result.stderr
