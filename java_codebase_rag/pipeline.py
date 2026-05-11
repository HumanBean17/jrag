"""Subprocess helpers for cocoindex + graph builder (no heavy ML imports at import time)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"


def bundle_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def cocoindex_bin() -> Path:
    return Path(sys.executable).parent / "cocoindex"


def run_cocoindex_update(
    env: dict[str, str],
    *,
    full_reprocess: bool,
    quiet: bool,
) -> subprocess.CompletedProcess[str]:
    exe = cocoindex_bin()
    if not exe.is_file():
        return subprocess.CompletedProcess(
            args=[str(exe)],
            returncode=127,
            stdout="",
            stderr=f"cocoindex not found next to Python: {exe}",
        )
    bd = bundle_dir()
    flow = bd / "java_index_flow_lancedb.py"
    if not flow.is_file():
        return subprocess.CompletedProcess(
            args=[],
            returncode=126,
            stdout="",
            stderr=f"java_index_flow_lancedb.py not found under {bd}",
        )
    cmd: list[str] = [str(exe), "update", COCOINDEX_TARGET]
    if full_reprocess:
        cmd.extend(["--full-reprocess", "-f"])
    else:
        cmd.append("-f")
    if quiet:
        cmd.append("-q")
    return subprocess.run(
        cmd,
        cwd=str(bd),
        env=env,
        capture_output=True,
        text=True,
    )


def run_cocoindex_drop(env: dict[str, str], *, quiet: bool) -> subprocess.CompletedProcess[str]:
    exe = cocoindex_bin()
    if not exe.is_file():
        return subprocess.CompletedProcess(
            args=[str(exe)],
            returncode=127,
            stdout="",
            stderr=f"cocoindex not found next to Python: {exe}",
        )
    bd = bundle_dir()
    cmd = [str(exe), "drop", COCOINDEX_TARGET, "-f"]
    if quiet:
        cmd.append("-q")
    return subprocess.run(
        cmd,
        cwd=str(bd),
        env=env,
        capture_output=True,
        text=True,
    )


def run_build_ast_graph(
    *,
    source_root: Path,
    kuzu_path: Path,
    verbose: bool,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    builder = bundle_dir() / "build_ast_graph.py"
    if not builder.is_file():
        return subprocess.CompletedProcess(
            args=[],
            returncode=126,
            stdout="",
            stderr=f"build_ast_graph.py not found under {builder.parent}",
        )
    cmd: list[str] = [
        sys.executable,
        str(builder),
        "--source-root",
        str(source_root),
        "--kuzu-path",
        str(kuzu_path),
    ]
    if verbose:
        cmd.append("--verbose")
    return subprocess.run(
        cmd,
        cwd=str(source_root),
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
    )


def clip(s: str, n: int) -> str:
    return s[-n:] if len(s) > n else s
