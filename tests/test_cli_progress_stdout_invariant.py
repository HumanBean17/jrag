from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from java_codebase_rag.cli_progress import accumulate_and_relay_subprocess_streams

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "cli_progress_stdout"


def _cocoindex_available() -> bool:
    return (Path(sys.executable).parent / "cocoindex").is_file()


def _run_cli(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("java-codebase-rag")
    assert exe is not None
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


async def test_stream_relay_arrives_before_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[bytes] = []
    orig_write = sys.stderr.buffer.write

    def capture_write(data: bytes | bytearray) -> int:
        recorded.append(bytes(data))
        return orig_write(data)

    monkeypatch.setattr(sys.stderr.buffer, "write", capture_write)

    code = (
        "import sys, time\n"
        "sys.stdout.buffer.write(b'EARLY\\n')\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(0.35)\n"
        "sys.stdout.buffer.write(b'LATE\\n')\n"
        "sys.stdout.buffer.flush()\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    acc_task = asyncio.create_task(accumulate_and_relay_subprocess_streams(proc, relay=True))
    joined = b""
    for _ in range(200):
        joined = b"".join(recorded)
        if b"EARLY" in joined:
            break
        await asyncio.sleep(0.02)
    assert b"EARLY" in joined, joined
    await acc_task
    final = b"".join(recorded)
    assert b"LATE" in final


def test_refresh_pipeline_quiet_stderr_baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import server

    repo_root = Path(__file__).resolve().parent.parent
    idx = tmp_path / "idx_q"
    idx.mkdir(parents=True)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(repo_root))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))

    real_is_file = Path.is_file

    def is_file_patched(self: Path) -> bool:
        try:
            if self.resolve() == (Path(sys.executable).parent / "cocoindex").resolve():
                return True
        except OSError:
            pass
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file_patched)

    async def fake_create(*_a: object, **_k: object) -> object:
        class _Proc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"idx_out", b"idx_err"

        return _Proc()

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_create)

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = asyncio.run(server.run_refresh_pipeline(quiet=True))
    err = buf.getvalue()
    assert "[lance]" not in err
    assert b"idx_out".decode() not in err
    assert b"idx_err".decode() not in err
    assert out.success is True
    assert "idx_out" in out.stdout


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_cli_lifecycle_stdout_invariant_init(corpus_root: Path, tmp_path: Path) -> None:
    baseline = (_FIXTURE_DIR / "init_quiet_success.stdout.txt").read_text(encoding="utf-8")
    idx = tmp_path / "stdout_inv_init"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())
    e0 = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes"],
        env=env,
    )
    assert e0.returncode == 0, e0.stderr
    proc = _run_cli(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout == baseline


def test_cli_lifecycle_stdout_invariant_reprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server
    from java_codebase_rag import cli as cli_mod
    from server import RefreshIndexOutput

    baseline = (_FIXTURE_DIR / "reprocess_quiet_success.stdout.txt").read_text(encoding="utf-8")

    async def fake_refresh(*, quiet: bool = False) -> RefreshIndexOutput:
        _ = quiet
        return RefreshIndexOutput(
            success=True,
            exit_code=0,
            stdout="",
            stderr="",
            message=None,
            graph_exit_code=0,
            graph_stdout="",
            graph_stderr="",
            phases_run=["vectors", "graph"],
        )

    monkeypatch.setattr(server, "run_refresh_pipeline", fake_refresh)

    repo_root = Path(__file__).resolve().parent.parent
    idx = tmp_path / "idx_rep_stdout"
    idx.mkdir(parents=True)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(repo_root))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(idx))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_mod.main(
            [
                "reprocess",
                "--source-root",
                str(repo_root),
                "--index-dir",
                str(idx),
                "--quiet",
            ],
        )
    assert rc == 0
    assert buf.getvalue() == baseline


def test_cli_lifecycle_stdout_invariant_erase_quiet(tmp_path: Path) -> None:
    idx = tmp_path / "idx_so"
    idx.mkdir()
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(tmp_path)
    proc = _run_cli(
        ["erase", "--source-root", str(tmp_path), "--index-dir", str(idx), "--yes", "--quiet"],
        env=env,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == '{"message": "erase completed", "success": true}'


@pytest.mark.skipif(not _cocoindex_available(), reason="cocoindex not installed in venv")
def test_cli_lifecycle_stdout_invariant_init_increment_reprocess_when_cocoindex(
    tmp_path: Path,
    corpus_root: Path,
) -> None:
    idx = tmp_path / "idx_inv"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(idx)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root.resolve())

    r_pre = _run_cli(
        ["erase", "--source-root", str(corpus_root), "--index-dir", str(idx), "--yes", "--quiet"],
        env=env,
    )
    assert r_pre.returncode == 0, r_pre.stderr

    r_init = _run_cli(
        ["init", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert r_init.returncode == 0, r_init.stderr + r_init.stdout
    assert r_init.stdout.strip() == '{"message": "init completed", "success": true}'

    r_inc = _run_cli(
        ["increment", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert r_inc.returncode == 0, r_inc.stderr + r_inc.stdout
    inc_payload = json.loads(r_inc.stdout)
    assert inc_payload == {
        "success": True,
        "message": "increment completed (Lance only; graph may be stale — see stderr)",
    }

    r_rep = _run_cli(
        ["reprocess", "--source-root", str(corpus_root), "--index-dir", str(idx), "--quiet"],
        env=env,
    )
    assert r_rep.returncode == 0, r_rep.stderr + r_rep.stdout
    rep_payload = json.loads(r_rep.stdout)
    assert rep_payload.get("success") is True
    assert isinstance(rep_payload.get("stdout"), str)
    assert isinstance(rep_payload.get("graph_stderr"), str)
