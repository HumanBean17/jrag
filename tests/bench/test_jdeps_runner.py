"""Tests for ``bench.oracle.jdeps_runner`` — JDK-native dependency-pair oracle.

Requires a compiled ``.class`` tree (javac). Marked ``requires_jdk``; the test
compiles the shared ``calls_demo`` fixture itself.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bench.oracle.jdeps_runner import OracleError, run

CALLS_DEMO = (
    Path(__file__).resolve().parents[2]
    / "tests" / "bench" / "fixtures" / "synthetic" / "calls_demo"
)


def _compile_calls_demo(tmp_path: Path) -> Path:
    classes = tmp_path / "classes"
    classes.mkdir()
    sources = [str(p) for p in CALLS_DEMO.rglob("*.java")]
    assert sources, "calls_demo fixture missing"
    subprocess.run(["javac", "-d", str(classes), *sources], check=True)
    return classes


@pytest.mark.requires_jdk
def test_parses_dependency_pairs(tmp_path):
    classes = _compile_calls_demo(tmp_path)
    pairs = run(str(classes))
    # Caller constructs a Callee -> Caller depends on Callee.
    assert ("call.Caller", "call.Callee") in pairs
    # All pairs are (dependent_fqn, dependency_fqn) strings.
    assert all(isinstance(a, str) and isinstance(b, str) for a, b in pairs)


@pytest.mark.requires_jdk
def test_package_prefix_filters(tmp_path):
    classes = _compile_calls_demo(tmp_path)
    pairs = run(str(classes), package_prefix="call")
    assert ("call.Caller", "call.Callee") in pairs
    # With a project prefix, JDK deps (java.lang.*) are excluded from both sides.
    assert all(a.startswith("call.") and b.startswith("call.") for a, b in pairs)


def test_missing_jdeps_raises(monkeypatch):
    monkeypatch.setattr("bench.oracle.jdeps_runner.shutil.which", lambda _: None)
    with pytest.raises(OracleError):
        run("does-not-matter")
