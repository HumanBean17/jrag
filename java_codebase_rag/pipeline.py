"""Subprocess helpers for cocoindex + graph builder (no heavy ML imports at import time)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from java_codebase_rag.cli_progress import emit_lance_cocoindex_finish, emit_lance_cocoindex_start

COCOINDEX_TARGET = "java_index_flow_lancedb.py:JavaCodeIndexLance"


def bundle_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def cocoindex_bin() -> Path:
    return Path(sys.executable).parent / "cocoindex"


def _popen_stream_to_stderr(
    proc: subprocess.Popen[bytes],
) -> tuple[str, str, int]:
    out_buf = bytearray()
    err_buf = bytearray()

    def drain_out() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            out_buf.extend(chunk)
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()

    def drain_err() -> None:
        assert proc.stderr is not None
        while True:
            chunk = proc.stderr.read(65536)
            if not chunk:
                break
            err_buf.extend(chunk)
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()

    t_out = threading.Thread(target=drain_out, name="stream-stdout", daemon=True)
    t_err = threading.Thread(target=drain_err, name="stream-stderr", daemon=True)
    t_out.start()
    t_err.start()
    t_out.join()
    t_err.join()
    code = proc.wait()
    return out_buf.decode(errors="replace"), err_buf.decode(errors="replace"), code


def run_cocoindex_update(
    env: dict[str, str],
    *,
    full_reprocess: bool,
    quiet: bool,
    lance_project_root: Path | None = None,
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

    emit_lance = lance_project_root is not None
    if emit_lance:
        emit_lance_cocoindex_start(lance_project_root)
    t0 = time.perf_counter()
    code = -1
    out_s, err_s = "", ""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(bd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        out_s, err_s, code = _popen_stream_to_stderr(proc)
    finally:
        if emit_lance:
            emit_lance_cocoindex_finish(elapsed_s=time.perf_counter() - t0, exit_code=code)
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


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
    if not verbose:
        return subprocess.run(
            cmd,
            cwd=str(source_root),
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
        )
    proc = subprocess.Popen(
        cmd,
        cwd=str(source_root),
        env=env or os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    out_s, err_s, code = _popen_stream_to_stderr(proc)
    return subprocess.CompletedProcess(args=cmd, returncode=code, stdout=out_s, stderr=err_s)


def clip(s: str, n: int) -> str:
    return s[-n:] if len(s) > n else s
