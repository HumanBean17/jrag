"""Tests for agent artifacts sync script.

Skill/agent consumer artifacts were removed (the CLI surface ships a
``jrag prime`` SessionStart hook, the MCP surface a server entry — neither
deploys files). ``SYNC_MAP`` is empty and the script is now an absence
guard, so these tests pin that contract:

- ``--check`` is green at HEAD
- a file reintroduced on either side (dev tree or install_data) fails it
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Driven by the script's own GUARDED_DIRS so the test cannot drift from it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.sync_agent_artifacts import GUARDED_DIRS  # noqa: E402



# Paths relative to repo root
SYNC_SCRIPT = Path("scripts/sync_agent_artifacts.py")


def run_sync_script(*, check: bool = False, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the sync script and return the result.

    Args:
        check: Pass --check flag (verify only, no writes)
        cwd: Working directory (defaults to repo root if None)

    Returns:
        CompletedProcess with stdout/stderr captured as text.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    if cwd is None:
        cwd = repo_root

    cmd = [sys.executable, str(repo_root / SYNC_SCRIPT)]
    if check:
        cmd.append("--check")

    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",  # Script emits UTF-8 (✓ marker); decode as such, not the locale ANSI codepage (cp1252 on Windows).
    )


def test_install_data_artifacts_in_sync_with_dev_source():
    """Baseline: --check passes at HEAD (no artifacts shipped on either side)."""
    result = run_sync_script(check=True)

    assert result.returncode == 0, (
        f"Sync check failed - artifacts out of sync.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    assert "✓ All agent artifacts in sync" in result.stdout, (
        f"Expected success message not found in stdout.\n"
        f"stdout: {result.stdout}"
    )


@pytest.mark.parametrize("guarded_dir", GUARDED_DIRS, ids=str)
def test_sync_script_flags_reintroduced_artifact(guarded_dir):
    """Verify --check exits non-zero when an artifact reappears.

    One case per guarded directory — both dev-side trees and their
    install_data mirrors. Seeds a stray file into a fresh temp workspace and
    runs the script with ``cwd`` pointed there, so the repo is never mutated.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        stray = tmp_path / guarded_dir / "SKILL.md"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("# this should not be here")

        result = run_sync_script(check=True, cwd=tmp_path)

        assert result.returncode == 1, (
            f"[{guarded_dir}] Expected --check to exit non-zero on a stray "
            f"artifact, but got {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        output = result.stdout + result.stderr
        assert "unexpected artifact" in output.lower(), (
            f"[{guarded_dir}] Expected script to report the stray artifact.\n"
            f"output: {output}"
        )


def test_sync_script_green_when_guarded_dirs_absent():
    """An empty workspace (no guarded dirs at all) is a pass, not an error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = run_sync_script(check=True, cwd=Path(tmpdir))

        assert result.returncode == 0, (
            f"Expected --check to pass on an empty workspace.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "✓ All agent artifacts in sync" in result.stdout
