"""Generated EDGE-NAVIGATION.md stability (SCHEMA-V2 PR-A)."""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GENERATOR = _REPO_ROOT / "scripts" / "generate_edge_navigation.py"
_COMMITTED = _REPO_ROOT / "docs" / "EDGE-NAVIGATION.md"
_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"


def test_edge_navigation_doc_matches_generator_output() -> None:
    result = subprocess.run(
        [str(_PYTHON), str(_GENERATOR), "--check"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert _COMMITTED.is_file()


def test_edge_navigation_doc_check_mode_detects_drift(tmp_path: Path) -> None:
    stale = tmp_path / "EDGE-NAVIGATION.md"
    stale.write_text("# stale\n", encoding="utf-8")
    result = subprocess.run(
        [str(_PYTHON), str(_GENERATOR), "--check", "--out", str(stale)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "stale" in (result.stderr or result.stdout).lower()
