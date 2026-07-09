"""Graph-only boot: the MCP server must start and serve graph tools without the vector
stack (lancedb/torch/sentence-transformers/cocoindex), which is the macOS Intel reality.

We simulate that install by pre-seeding ``sys.modules[<name>] = None`` in a fresh
subprocess, which makes ``import <name>`` raise ``ModuleNotFoundError``. This runs on any
platform (no uninstall needed) and proves the lazy-import seam in ``server.py``/``mcp_v2.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from java_codebase_rag.pipeline import is_cocoindex_preflight_blocker

# Absent on a graph-only install. Pre-seeding with None forces ImportError on import.
_VECTOR_MODULES = ("lancedb", "pylance", "torch", "sentence_transformers", "cocoindex")

_BOOT_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys

    for _m in {modules!r}:
        sys.modules[_m] = None  # force ImportError on import

    from java_codebase_rag.mcp import server  # must not pull the vector stack at module load
    srv = server.create_mcp_server()  # registers tools + runs ScopeManager setup

    loaded = [m for m in {vector!r} if sys.modules.get(m) is not None]
    tools = sorted(t.name for t in asyncio.run(srv.list_tools()))
    from java_codebase_rag.mcp.mcp_v2 import search_v2
    out = search_v2(query="x")
    print("TOOLS:" + ",".join(tools))
    print("LOADED_AT_BOOT:" + (",".join(loaded) or "none"))
    print("SEARCH_SUCCESS:" + str(out.success))
    print("SEARCH_MSG:" + str(out.message))
    """
)


def _run_graph_only_boot() -> subprocess.CompletedProcess[str]:
    # Point the index dir at a nonexistent path so the lexical backend's
    # `LadybugGraph.exists()` is deterministically False regardless of the dev
    # machine's state — the boot test asserts the no-graph clean-failure path.
    env = {**os.environ, "JAVA_CODEBASE_RAG_INDEX_DIR": "/tmp/lex_boot_no_graph"}
    return subprocess.run(
        [sys.executable, "-c", _BOOT_SCRIPT.format(modules=_VECTOR_MODULES, vector=_VECTOR_MODULES)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_server_boots_and_serves_graph_tools_without_vector_stack() -> None:
    proc = _run_graph_only_boot()
    assert proc.returncode == 0, f"graph-only boot failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"

    out = proc.stdout
    # All five tools register; `search` is present but degrades at call time (below).
    assert "TOOLS:describe,find,neighbors,resolve,search" in out
    # None of the vector modules may be imported during boot.
    assert "LOADED_AT_BOOT:none" in out
    # The search tool returns a clean failure rather than raising. In graph-only mode
    # search dispatches to the lexical backend, which (with no graph present) reports
    # "lexical search unavailable" instead of the old "Vector search unavailable".
    assert "SEARCH_SUCCESS:False" in out
    assert "lexical search unavailable" in out


def _completed(returncode: int, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=list(args), returncode=returncode, stdout="", stderr="")


def test_preflight_blocker_detects_graph_only_install() -> None:
    # The detector drives init/increment/install/update's "skip vectors, build graph"
    # branch. It must recognize the two pre-spawn stubs (cocoindex binary missing on a
    # graph-only install; flow file missing) and NOT mistake a real cocoindex run for one.
    exe = "/example/.venv/bin/cocoindex"

    assert is_cocoindex_preflight_blocker(_completed(127, (exe,))) is True   # binary absent
    assert is_cocoindex_preflight_blocker(_completed(126, ())) is True        # flow file absent
    # A real cocoindex invocation carries the full command list (len(args) > 1): a genuine
    # non-zero exit is a failure, not a skip.
    assert is_cocoindex_preflight_blocker(_completed(1, (exe, "update", "t", "-f"))) is False
    assert is_cocoindex_preflight_blocker(_completed(0, (exe, "update", "t", "-f"))) is False


def test_vector_stack_installed_reports_absent_when_blocked() -> None:
    # The installer wizard gates its embedding-model step on vector_stack_installed().
    # Deterministic across platforms: blocking the modules in a fresh subprocess must
    # report False (this is the macOS Intel reality).
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "for m in ('lancedb','pylance','torch','sentence_transformers','cocoindex'):\n"
            "    sys.modules[m] = None\n"
            "from java_codebase_rag.pipeline import vector_stack_installed\n"
            "print('INSTALLED:' + str(vector_stack_installed()))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "INSTALLED:False" in proc.stdout


def test_refresh_pipeline_skips_vectors_and_builds_graph_on_graph_only(
    monkeypatch, tmp_path
) -> None:
    """``reprocess``'s refresh pipeline must skip vectors and build the graph on a
    graph-only install (macOS Intel), NOT fail with a cryptic "cocoindex not found"
    message. Mirrors init/increment's skip-vectors-then-build-graph branch; this is
    the regression guard for the Intel-Mac reprocess failure.
    """
    import asyncio
    import io
    from contextlib import redirect_stderr
    from pathlib import Path

    from java_codebase_rag.mcp import server

    monkeypatch.setattr(server, "vector_stack_installed", lambda: False)

    captured: dict[str, object] = {}

    async def fake_graph_phase(root, *, quiet, verbose, on_progress, on_progress_console):
        captured["called"] = True
        captured["root"] = root
        return 0, "GRAPH_STDOUT", "GRAPH_STDERR", True

    monkeypatch.setattr(server, "_run_graph_phase", fake_graph_phase)

    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(repo_root))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(tmp_path / "idx"))

    buf = io.StringIO()
    with redirect_stderr(buf):
        out = asyncio.run(server.run_refresh_pipeline(quiet=True))
    err = buf.getvalue()

    # Vectors were skipped; the graph phase ran (got the resolved source root) and
    # succeeded.
    assert captured.get("called") is True
    assert captured.get("root") == repo_root
    assert out.success is True
    assert out.phases_run == ["graph"]
    assert out.graph_exit_code == 0
    # The operator-facing skip line is printed; the cryptic failure is not, anywhere.
    assert "vectors skipped" in err
    assert "graph-only mode" in err
    assert "cocoindex not found" not in err
    assert "cocoindex not found" not in (out.message or "")
    # The JSON message documents the graph-only outcome.
    assert "graph-only" in (out.message or "")
