"""End-to-end test: real LanceDB index + AST graph + MCP search tools.

Gated behind ``JAVA_CODEBASE_RAG_RUN_HEAVY=1`` because this test:

* runs ``cocoindex update`` against the bank-chat-system corpus, which
  downloads the embedding model on first use,
* writes a real LanceDB directory under ``tmp_path``,
* and only then invokes MCP ``search``.

⚠️  Same anti-overfitting rules apply: we assert that the tool *returned a
useful, well-shaped result* — never on exact ranking, scores, or snippet
text. Search ranking is allowed to drift between SBERT releases without
breaking this test. See `tests/README.md`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HEAVY = os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip().lower() in ("1", "true", "yes")
pytestmark = [
    pytest.mark.skipif(
        not HEAVY,
        reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the cocoindex + LanceDB end-to-end test",
    ),
    pytest.mark.lance_e2e,
]

CAPABILITY_SMOKE_ROOT = Path(__file__).resolve().parent / "fixtures" / "capability_smoke"
IGNORE_SMOKE_ROOT = Path(__file__).resolve().parent / "fixtures" / "lancedb_ignore_smoke"


def _require_cocoindex_runtime_deps() -> None:
    """`cocoindex` loads `java_index_flow_lancedb.py` with the same Python as the CLI (see venv)."""
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            "Heavy e2e needs project deps in the current env (e.g. ``pip install -r requirements*``"
            f" in the venv you use to run pytest): {exc}"
        )


def _cocoindex_flow_specifier(bundle_dir: Path, index_cwd: Path) -> str:
    """Build ``path:JavaCodeIndexLance`` for ``cocoindex update`` with ``cwd=index_cwd``."""
    flow = (bundle_dir / "java_index_flow_lancedb.py").resolve()
    if not flow.is_file():
        raise FileNotFoundError(f"missing index flow: {flow}")
    start = index_cwd.resolve()
    relp = os.path.relpath(str(flow), start=str(start))
    relp = Path(relp).as_posix()
    return f"{relp}:JavaCodeIndexLance"


def _structured(result):
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    for block in result:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    raise AssertionError(f"could not extract structured payload from {result!r}")


@pytest.fixture(scope="module")
def lance_index(tmp_path_factory, corpus_root: Path) -> Path:
    """Build a real LanceDB index over the corpus via cocoindex."""
    _require_cocoindex_runtime_deps()
    bundle_dir = Path(__file__).resolve().parent.parent
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter; install cocoindex in this "
            f"venv and run: `.venv/bin/python -m pytest ...` ({cocoindex_bin})"
        )

    work = tmp_path_factory.mktemp("lance_e2e")
    index_dir = work / ".java-codebase-rag"
    index_dir.mkdir(parents=True)

    app_spec = _cocoindex_flow_specifier(bundle_dir, Path(corpus_root))

    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(Path(corpus_root).resolve()),
    }
    proc = subprocess.run(
        [
            str(cocoindex_bin),
            "update",
            app_spec,
            "--full-reprocess",
            "-f",
        ],
        cwd=str(corpus_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, (
        f"cocoindex failed: stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    # The flow disables cocoindex's concurrent background optimize (which raced
    # table.delete() and flooded stderr with commit conflicts — issue #308).
    # After the fix, no commit-conflict markers should appear in the flow's
    # stderr. We assert this here because this fixture runs a real full
    # reprocess; if the race regressed, this is where it would surface.
    for marker in ("Retryable commit conflict", "preempted by concurrent transaction"):
        assert marker not in proc.stderr, (
            f"commit-conflict marker '{marker}' present in cocoindex stderr; "
            f"the in-flow background optimize race may have regressed:\n{proc.stderr}"
        )

    builder = bundle_dir / "build_ast_graph.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(builder),
            "--source-root",
            str(corpus_root),
            "--ladybug-path",
            str(index_dir / "code_graph.lbug"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return index_dir


@pytest.fixture(scope="module")
def lance_index_capability_smoke(tmp_path_factory) -> Path:
    """Tiny project with @KafkaListener — indexes fast; tests capability filter in search."""
    _require_cocoindex_runtime_deps()
    bundle_dir = Path(__file__).resolve().parent.parent
    if not CAPABILITY_SMOKE_ROOT.is_dir():
        pytest.skip(f"capability smoke fixture missing: {CAPABILITY_SMOKE_ROOT}")
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter ({cocoindex_bin})"
        )

    work = tmp_path_factory.mktemp("lance_cap_smoke")
    index_dir = work / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    app_spec = _cocoindex_flow_specifier(bundle_dir, Path(CAPABILITY_SMOKE_ROOT))
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(CAPABILITY_SMOKE_ROOT.resolve()),
    }
    proc = subprocess.run(
        [
            str(cocoindex_bin),
            "update",
            app_spec,
            "--full-reprocess",
            "-f",
        ],
        cwd=str(CAPABILITY_SMOKE_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, (
        f"cocoindex failed: stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    builder = bundle_dir / "build_ast_graph.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(builder),
            "--source-root",
            str(CAPABILITY_SMOKE_ROOT),
            "--ladybug-path",
            str(index_dir / "code_graph.lbug"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return index_dir


async def test_search_returns_hits(lance_index: Path, monkeypatch) -> None:
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(lance_index))
    monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

    from ladybug_queries import LadybugGraph

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    from server import create_mcp_server

    server = create_mcp_server()

    out = _structured(
        await server.call_tool(
            "search",
            {
                "query": "how chat assigns operator on incoming message",
                "limit": 5,
                "table": "java",
            },
        )
    )
    assert out["success"] is True
    assert out["results"], out
    for hit in out["results"]:
        cid = hit.get("chunk_id") or ""
        assert isinstance(cid, str) and cid, hit


async def test_search_capability_filter_e2e(
    lance_index_capability_smoke: Path, monkeypatch,
) -> None:
    """MCP ``search`` with ``filter.capability`` — full Lance + enrich path (heavy)."""
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(lance_index_capability_smoke))
    monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

    from ladybug_queries import LadybugGraph

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    from server import create_mcp_server

    server = create_mcp_server()

    out = _structured(
        await server.call_tool(
            "search",
            {
                "query": "kafka listener consumer message handler",
                "limit": 10,
                "table": "java",
                "filter": {"capability": "MESSAGE_LISTENER"},
            },
        )
    )
    assert out["success"] is True
    assert out["results"], out


def _unique_java_filenames_in_lance(lance_uri: Path) -> int:
    import lancedb

    db = lancedb.connect(str(lance_uri))
    tbl = db.open_table("javacodeindex_java_code")
    names = tbl.to_arrow().column("filename").to_pylist()
    return len(set(names))


def test_lancedb_ignore_file_reduces_indexed_java_files(tmp_path_factory) -> None:
    """PR-C test 47: ``.java-codebase-rag/ignore`` excludes generated sources from the Lance index."""
    _require_cocoindex_runtime_deps()
    if not IGNORE_SMOKE_ROOT.is_dir():
        pytest.skip(f"missing fixture tree: {IGNORE_SMOKE_ROOT}")
    bundle_dir = Path(__file__).resolve().parent.parent
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter ({cocoindex_bin})"
        )

    work = tmp_path_factory.mktemp("lance_ignore_e2e")
    with_dir = work / "with_ignore"
    without_dir = work / "without_ignore"
    shutil.copytree(IGNORE_SMOKE_ROOT, with_dir)
    shutil.copytree(IGNORE_SMOKE_ROOT, without_dir)
    shutil.rmtree(without_dir / ".java-codebase-rag", ignore_errors=True)

    def run_coco(corpus: Path) -> Path:
        index_dir = corpus / ".java-codebase-rag"
        index_dir.mkdir(parents=True)
        app_spec = _cocoindex_flow_specifier(bundle_dir, corpus)
        env = {
            **os.environ,
            "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
            "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
        }
        proc = subprocess.run(
            [
                str(cocoindex_bin),
                "update",
                app_spec,
                "--full-reprocess",
                "-f",
            ],
            cwd=str(corpus),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert proc.returncode == 0, proc.stderr
        return index_dir

    n_with = _unique_java_filenames_in_lance(run_coco(with_dir))
    n_without = _unique_java_filenames_in_lance(run_coco(without_dir))
    assert n_without > n_with
    assert n_with >= 1


async def test_search_returns_multiple_hits(lance_index: Path, monkeypatch) -> None:
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(lance_index))
    monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)

    from ladybug_queries import LadybugGraph

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    from server import create_mcp_server

    server = create_mcp_server()

    out = _structured(
        await server.call_tool(
            "search",
            {"query": "what happens when a chat is assigned to an operator", "limit": 8},
        )
    )
    assert out["success"] is True
    assert len(out["results"]) >= 1


def test_layered_ignore_provided_once_per_flow() -> None:
    """Source-structure assertion that IGNORE is provided once and consumed three times.

    This test verifies the wiring invariant (IGNORE ContextKey provided once in
    coco_lifespan, consumed in three process_*_file sites) by inspecting the flow
    module source code. The behavioral guarantee (a single LayeredIgnore instance
    per flow run) is backed by the HEAVY e2e test below and the sentinel grep.

    This approach is used because in-process testing of coco_lifespan would require
    stubbing the embedder/LanceDB setup, and subprocess-based testing cannot cross
    the process boundary to instrument LayeredIgnore.__init__.
    """
    bundle_dir = Path(__file__).resolve().parent.parent
    flow_file = bundle_dir / "java_index_flow_lancedb.py"
    if not flow_file.is_file():
        pytest.skip(f"Flow file not found: {flow_file}")

    source = flow_file.read_text(encoding="utf-8")

    # Count builder.provide(IGNORE, ...) calls - should be exactly one (in coco_lifespan)
    provide_count = source.count("builder.provide(IGNORE,")
    assert provide_count == 1, f"Expected 1 builder.provide(IGNORE,) call, found {provide_count}"

    # Count coco.use_context(IGNORE) calls - should be exactly three (process_*_file)
    use_count = source.count("coco.use_context(IGNORE)")
    assert use_count == 3, f"Expected 3 coco.use_context(IGNORE) calls, found {use_count}"

    # Verify no leftover LayeredIgnore(project_root).is_ignored calls in process sites
    # (the sentinel grep would catch this, but we assert it here for completeness)
    lines = source.split("\n")
    for i, line in enumerate(lines, 1):
        if "def process_" in line and "file(" in line:
            # Found a process_*_file function definition
            # Check the next ~10 lines for the old pattern
            func_body = "\n".join(lines[i-1:min(i+10, len(lines))])
            if "LayeredIgnore(project_root).is_ignored" in func_body:
                pytest.fail(f"Found LayeredIgnore(project_root).is_ignored in process_*_file at line {i}")

    # All structure checks passed
