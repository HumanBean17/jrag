"""Tests for agent artifacts sync script.

Validates that:
- Dev source and install_data copies stay in sync
- The sync script detects drift correctly
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path



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
    repo_root = Path(__file__).resolve().parent.parent
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
    """Baseline: --check passes at HEAD (dev source and install_data are byte-equal)."""
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


def _seed_dev_source(tmp_path: Path, *, cli_skill_content: str = "# test") -> None:
    """Create the canonical dev source tree the SYNC_MAP expects.

    The sync script walks ``SYNC_MAP`` source dirs; PR-JRAG-5 added
    ``skills/explore-codebase-cli`` to that map, so synthetic temp workspaces
    used by the drift tests must seed it too.
    """
    tmp_agents = tmp_path / "agents"
    tmp_agents.mkdir(parents=True, exist_ok=True)
    (tmp_agents / "explorer-rag-enhanced.md").write_text("# test")

    tmp_skills = tmp_path / "skills" / "explore-codebase"
    tmp_skills.mkdir(parents=True, exist_ok=True)
    (tmp_skills / "SKILL.md").write_text("# test")

    tmp_cli_skills = tmp_path / "skills" / "explore-codebase-cli"
    tmp_cli_skills.mkdir(parents=True, exist_ok=True)
    (tmp_cli_skills / "SKILL.md").write_text(cli_skill_content)


def _seed_install_data(tmp_path: Path, *, extra: list[Path] | None = None) -> None:
    """Create the matching install_data tree (no drift) for the SYNC_MAP."""
    tmp_install_agents = tmp_path / "java_codebase_rag" / "install_data" / "agents"
    tmp_install_agents.mkdir(parents=True, exist_ok=True)
    (tmp_install_agents / "explorer-rag-enhanced.md").write_text("# test")

    tmp_install_mcp_skill = (
        tmp_path / "java_codebase_rag" / "install_data" / "skills" / "explore-codebase"
    )
    tmp_install_mcp_skill.mkdir(parents=True, exist_ok=True)
    (tmp_install_mcp_skill / "SKILL.md").write_text("# test")

    tmp_install_cli_skill = (
        tmp_path / "java_codebase_rag" / "install_data" / "skills" / "explore-codebase-cli"
    )
    tmp_install_cli_skill.mkdir(parents=True, exist_ok=True)
    (tmp_install_cli_skill / "SKILL.md").write_text("# test")

    for path in extra or []:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# this should not be here")


def test_sync_script_detects_drift():
    """Verify --check exits non-zero when dev source and install_data differ.

    This test:
    1. Copies a real dev source file to a temp dir
    2. Mutates a byte in the temp copy
    3. Points the sync script at the mutated tree via cwd override
    4. Asserts --check exits non-zero AND names the offending file
    5. Restores by temp dir auto-cleanup (no repo mutation)
    """
    repo_root = Path(__file__).resolve().parent.parent

    # Copy a real file (agents/explorer-rag-enhanced.md) to temp workspace
    real_dev_file = repo_root / "agents" / "explorer-rag-enhanced.md"
    real_skill_file = repo_root / "skills" / "explore-codebase" / "SKILL.md"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Create the agents directory structure in temp
        tmp_agents = tmp_path / "agents"
        tmp_agents.mkdir()

        # Copy real file to temp and mutate it
        tmp_file = tmp_agents / "explorer-rag-enhanced.md"
        tmp_file.write_bytes(real_dev_file.read_bytes())

        # Mutate a byte (change first character if it's ASCII, otherwise append)
        original_content = tmp_file.read_text(encoding="utf-8")
        if original_content:
            mutated_content = "X" + original_content[1:]
        else:
            mutated_content = "X"
        tmp_file.write_text(mutated_content, encoding="utf-8")

        # Create skills/explore-codebase directory (unchanged, for completeness)
        tmp_skills = tmp_path / "skills" / "explore-codebase"
        tmp_skills.mkdir(parents=True)
        (tmp_skills / "SKILL.md").write_bytes(real_skill_file.read_bytes())

        # PR-JRAG-5: SYNC_MAP also walks skills/explore-codebase-cli — seed it.
        tmp_cli_skills = tmp_path / "skills" / "explore-codebase-cli"
        tmp_cli_skills.mkdir(parents=True)
        (tmp_cli_skills / "SKILL.md").write_text("# test")

        # Also create the install_data directory structure in temp
        # so the script has something to compare against
        tmp_install = tmp_path / "java_codebase_rag" / "install_data" / "agents"
        tmp_install.mkdir(parents=True)

        # Copy the unmutated file to install_data
        (tmp_install / "explorer-rag-enhanced.md").write_bytes(real_dev_file.read_bytes())

        tmp_install_skills = tmp_path / "java_codebase_rag" / "install_data" / "skills" / "explore-codebase"
        tmp_install_skills.mkdir(parents=True)
        (tmp_install_skills / "SKILL.md").write_bytes(real_skill_file.read_bytes())

        tmp_install_cli_skills = (
            tmp_path / "java_codebase_rag" / "install_data" / "skills" / "explore-codebase-cli"
        )
        tmp_install_cli_skills.mkdir(parents=True)
        (tmp_install_cli_skills / "SKILL.md").write_text("# test")

        # Run the sync script from temp directory (so it sees the mutated file)
        result = run_sync_script(check=True, cwd=tmp_path)

        # Should exit non-zero due to drift
        assert result.returncode == 1, (
            f"Expected --check to exit non-zero on drift, but got {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # Should mention the file that differs
        output = result.stdout + result.stderr
        assert "explorer-rag-enhanced.md" in output or "out of sync" in output, (
            f"Expected script to report the drifted file or 'out of sync'.\n"
            f"output: {output}"
        )


def test_sync_script_detects_extra_files():
    """Verify --check detects extra files in install_data that shouldn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Create dev source (agents + both skills — PR-JRAG-5 added CLI skill).
        _seed_dev_source(tmp_path)

        # Create install_data with an extra file
        _seed_install_data(
            tmp_path,
            extra=[tmp_path / "java_codebase_rag" / "install_data" / "agents" / "extra_file.md"],
        )

        result = run_sync_script(check=True, cwd=tmp_path)

        assert result.returncode == 1, (
            f"Expected --check to exit non-zero on extra files, but got {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        output = result.stdout + result.stderr
        assert "extra_file.md" in output or "extra file" in output.lower(), (
            f"Expected script to report the extra file.\n"
            f"output: {output}"
        )


def test_sync_script_detects_missing_files():
    """Verify --check detects missing files in install_data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Create dev source (agents + both skills — PR-JRAG-5 added CLI skill).
        _seed_dev_source(tmp_path)

        # Create empty install_data (missing the files)
        tmp_install = tmp_path / "java_codebase_rag" / "install_data" / "agents"
        tmp_install.mkdir(parents=True)

        tmp_install_skills = tmp_path / "java_codebase_rag" / "install_data" / "skills" / "explore-codebase"
        tmp_install_skills.mkdir(parents=True)

        tmp_install_cli_skills = (
            tmp_path / "java_codebase_rag" / "install_data" / "skills" / "explore-codebase-cli"
        )
        tmp_install_cli_skills.mkdir(parents=True)

        result = run_sync_script(check=True, cwd=tmp_path)

        assert result.returncode == 1, (
            f"Expected --check to exit non-zero on missing files, but got {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        output = result.stdout + result.stderr
        assert "explorer-rag-enhanced.md" in output or "missing" in output.lower(), (
            f"Expected script to report the missing file.\n"
            f"output: {output}"
        )
