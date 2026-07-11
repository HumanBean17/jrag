"""Tests for ``WarmResources`` (warm model/graph holders + graph COW snapshot lifecycle).

Gated behind ``JAVA_CODEBASE_RAG_RUN_HEAVY=1`` because the graph tests build a real
Ladybug index via the production AST pipeline (a subprocess) and the model test
loads a real ``SentenceTransformer``.

``WarmResources`` is the daemon's warm-resources holder (design §4.7): it keeps the
embedding model and the read-only Ladybug graph resident so queries are instant,
and it serves graph reads from a file COPY (sidecar) while a graph-reindex
subprocess writes the original. These tests pin each behavior the daemon relies on:

  (a) ``model()`` returns the SAME ``SentenceTransformer`` on repeated calls (warm).
  (b) ``graph()`` returns a ``LadybugGraph`` whose ``meta()`` succeeds.
  (c) ``begin_graph_snapshot()`` creates the ``.lbug.snapshot`` sidecar and
      ``graph()`` thereafter reads the sidecar — a subprocess write to the ORIGINAL
      is NOT reflected through ``graph()`` (the sidecar copy is frozen).
  (d) after the original is overwritten by a subprocess and
      ``commit_graph_snapshot()`` runs, ``graph()`` reads the UPDATED original and
      the sidecar file is gone.
  (e) ``LadybugGraph.reset_for_path(None)`` (and a matching path) clears the cached
      singleton so the next ``get`` reopens; a non-matching path is a no-op.

Node-count change across an incremental rebuild is measured by a DIRECT Cypher
``MATCH (s:Symbol) RETURN count(*)`` query, NOT ``LadybugGraph.meta()["counts"]``
(see ``test_concurrency_characterization._graph_symbol_count`` — phantom resolution
keeps the summed ``counts`` total flat while ``:Symbol`` grows).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# --- Heavy gate (mirrors tests/watch/test_concurrency_characterization.py) --------
HEAVY = (
    os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip().lower() in ("1", "true", "yes")
)
pytestmark = pytest.mark.skipif(
    not HEAVY,
    reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the WarmResources tests "
    "(build a real Ladybug graph + load a real embedding model)",
)

_TESTS_DIR = Path(__file__).resolve().parent.parent
_FIXTURE_CORPUS = _TESTS_DIR / "fixtures" / "call_graph_smoke"

# A minimal new Java source added between the initial build and the incremental
# rebuild so the graph's :Symbol count strictly grows. Stored under the corpus's
# own package so the AST builder picks it up.
_NEW_JAVA_REL = "src/main/java/smoke/WatchNewFile.java"
_NEW_JAVA_CONTENT = (
    "package smoke;\n"
    "\n"
    "public class WatchNewFile {\n"
    "    private final String name;\n"
    "\n"
    "    public WatchNewFile(String name) {\n"
    "        this.name = name;\n"
    "    }\n"
    "\n"
    "    public String greet() {\n"
    "        return \"hello \" + this.name;\n"
    "    }\n"
    "}\n"
)


# --- helpers --------------------------------------------------------------------


def _require_pipeline_deps() -> None:
    """Skip if the cocoindex binary or tree-sitter-java are absent (graph-only env)."""
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            "Heavy WarmResources tests need project deps in the current env "
            f"(pip install -e .[dev]): {exc}"
        )
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(f"cocoindex CLI not found next to the pytest interpreter ({cocoindex_bin})")


def _sandbox(tmp_path: Path, tag: str) -> tuple[Path, Path]:
    """Copy the fixture corpus into a temp dir; return (corpus, index_dir).

    Each test gets an isolated, mutable corpus copy and a fresh index dir.
    JAVA_CODEBASE_RAG_INDEX_DIR / SOURCE_ROOT are published via monkeypatch by the
    caller so ``resolve_operator_config`` resolves ``cfg.ladybug_path`` to
    ``<index_dir>/code_graph.lbug``.
    """
    _require_pipeline_deps()
    assert _FIXTURE_CORPUS.is_dir(), f"fixture corpus missing: {_FIXTURE_CORPUS}"
    corpus = tmp_path / f"corpus_{tag}"
    shutil.copytree(_FIXTURE_CORPUS, corpus)
    index_dir = tmp_path / f"index_{tag}" / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    return corpus, index_dir


def _graph_symbol_count(ladybug_path: Path) -> int:
    """Ground-truth ``:Symbol`` count via a fresh read-only Ladybug connection.

    A direct query is the reliable change measure across an incremental rebuild
    (``meta()["counts"]`` summed total can stay flat — see module docstring).
    """
    import ladybug

    conn = ladybug.Connection(ladybug.Database(str(ladybug_path), read_only=True))
    result = conn.execute("MATCH (s:Symbol) RETURN count(*) AS n")
    return int(result.get_next()[0]) if result.has_next() else 0


def _build_graph(corpus: Path, ladybug_path: Path) -> None:
    """Run the full AST graph build (subprocess) at ``ladybug_path``."""
    from java_codebase_rag.pipeline import run_build_ast_graph

    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(ladybug_path.parent.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
    }
    proc = run_build_ast_graph(
        source_root=corpus,
        ladybug_path=ladybug_path,
        verbose=False,
        quiet=True,
        env=env,
    )
    assert proc.returncode == 0, f"initial graph build failed: {proc.stderr}"


# ---------------------------------------------------------------- (a) model -----


def test_model_returns_same_instance(tmp_path: Path, monkeypatch) -> None:
    """``WarmResources.model()`` returns the same SentenceTransformer on every call.

    Inputs: a ``ResolvedOperatorConfig`` whose embedding model/device come from the
    environment; the module-global model cache is reset before the first call.
    Expected: two ``model()`` calls return the very same object (``is``), and the
    module global still holds that instance — warmth is reuse, not reload.
    """
    from java_codebase_rag.config import resolve_operator_config
    from java_codebase_rag.mcp import mcp_v2

    corpus = tmp_path / "corpus_model"
    shutil.copytree(_FIXTURE_CORPUS, corpus)
    index_dir = tmp_path / "index_model" / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir.resolve()))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus.resolve()))

    # Isolate the module-global cache for this test.
    mcp_v2._st_model = None

    cfg = resolve_operator_config(source_root=corpus)
    from java_codebase_rag.watch.warm import WarmResources

    warm = WarmResources(cfg)
    m1 = warm.model()
    m2 = warm.model()
    assert m1 is m2, "WarmResources.model() returned different objects across calls"
    assert mcp_v2._st_model is m1, "module-global model cache was not populated/reused"


# -------------------------------------------------------- (b) graph meta --------


def test_graph_returns_ladybug_whose_meta_succeeds(tmp_path: Path, monkeypatch) -> None:
    """``WarmResources.graph()`` returns a ``LadybugGraph`` with a working ``meta()``.

    Inputs: a built graph at ``cfg.ladybug_path``.
    Expected: ``graph()`` returns a ``LadybugGraph`` whose ``db_path`` is the config
    path and whose ``meta()`` returns a dict without an ``"error"`` key.
    """
    from java_codebase_rag.config import resolve_operator_config
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph

    corpus, index_dir = _sandbox(tmp_path, "gmeta")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir.resolve()))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus.resolve()))
    cfg = resolve_operator_config(source_root=corpus)
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    _build_graph(corpus, cfg.ladybug_path)

    from java_codebase_rag.watch.warm import WarmResources

    warm = WarmResources(cfg)
    graph = warm.graph()
    assert graph.db_path == str(cfg.ladybug_path), (
        f"graph().db_path={graph.db_path!r} != cfg.ladybug_path={str(cfg.ladybug_path)!r}"
    )
    meta = graph.meta()
    assert "error" not in meta, f"graph().meta() reported an error: {meta.get('error')}"


# ------------------------------------------- (c) begin_graph_snapshot ------------


def test_begin_graph_snapshot_serves_frozen_sidecar(tmp_path: Path, monkeypatch) -> None:
    """``begin_graph_snapshot()`` makes ``graph()`` read a frozen sidecar copy.

    Inputs: a built graph; then ``begin_graph_snapshot()``; then a subprocess
    incremental rebuild (adds a file) that writes the ORIGINAL.
    Expected after begin:
      * the ``.lbug.snapshot`` sidecar file exists beside the original;
      * ``graph()`` returns a reader whose ``db_path`` is the SIDECAR;
      * ``graph()``'s ``:Symbol`` count stays at the pre-write count (the sidecar is
        frozen), while a FRESH reader on the original sees the grown post-write count
        — proving ``graph()`` serves the sidecar, not the original.
    """
    from java_codebase_rag.config import resolve_operator_config
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph
    from java_codebase_rag.pipeline import run_incremental_graph

    corpus, index_dir = _sandbox(tmp_path, "snap")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir.resolve()))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus.resolve()))
    cfg = resolve_operator_config(source_root=corpus)
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    _build_graph(corpus, cfg.ladybug_path)
    pre_count = _graph_symbol_count(cfg.ladybug_path)
    assert pre_count > 0, "initial graph has no Symbol nodes"

    from java_codebase_rag.watch.warm import WarmResources

    warm = WarmResources(cfg)
    sidecar = cfg.ladybug_path.with_suffix(".lbug.snapshot")

    warm.begin_graph_snapshot()
    assert sidecar.is_file(), f"begin_graph_snapshot did not create sidecar: {sidecar}"
    assert warm.graph().db_path == str(sidecar), (
        f"after begin, graph().db_path={warm.graph().db_path!r} != sidecar={str(sidecar)!r}"
    )

    # Add a file and run the incremental rebuild against the ORIGINAL, with the
    # sidecar reader (held by the WarmResources) still active.
    (corpus / _NEW_JAVA_REL).parent.mkdir(parents=True, exist_ok=True)
    (corpus / _NEW_JAVA_REL).write_text(_NEW_JAVA_CONTENT, encoding="utf-8")
    inc_proc = run_incremental_graph(
        source_root=corpus,
        ladybug_path=cfg.ladybug_path,
        verbose=True,
        quiet=False,
        env={
            **os.environ,
            "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
            "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
        },
    )
    assert inc_proc.returncode == 0, (
        f"run_incremental_graph failed while sidecar reader was open: {inc_proc.stderr}"
    )

    # graph() reads the frozen sidecar -> still the pre-write count.
    sidecar_count_via_graph = _graph_symbol_count(Path(warm.graph().db_path))
    assert sidecar_count_via_graph == pre_count, (
        "graph() observed the original's write after begin_graph_snapshot — sidecar "
        f"isolation broken (graph={sidecar_count_via_graph}, pre={pre_count})"
    )

    # A fresh reader on the ORIGINAL sees the grown count.
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    post_count = _graph_symbol_count(cfg.ladybug_path)
    assert post_count > pre_count, (
        f"original :Symbol count did not grow after incremental rebuild "
        f"(pre={pre_count}, post={post_count})"
    )


# ------------------------------------------ (d) commit_graph_snapshot ------------


def test_commit_graph_snapshot_reopens_updated_original(tmp_path: Path, monkeypatch) -> None:
    """``commit_graph_snapshot()`` drops the sidecar and reopens the updated original.

    Inputs: a built graph; ``begin_graph_snapshot()``; an incremental rebuild that
    writes the ORIGINAL; then ``commit_graph_snapshot()``.
    Expected after commit:
      * the sidecar file is GONE;
      * ``graph()`` returns a reader on the ORIGINAL (db_path == cfg.ladybug_path)
        whose ``:Symbol`` count is the grown post-write count (the updated original).
    """
    from java_codebase_rag.config import resolve_operator_config
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph
    from java_codebase_rag.pipeline import run_incremental_graph

    corpus, index_dir = _sandbox(tmp_path, "commit")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir.resolve()))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus.resolve()))
    cfg = resolve_operator_config(source_root=corpus)
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    _build_graph(corpus, cfg.ladybug_path)
    pre_count = _graph_symbol_count(cfg.ladybug_path)
    assert pre_count > 0, "initial graph has no Symbol nodes"

    from java_codebase_rag.watch.warm import WarmResources

    warm = WarmResources(cfg)
    sidecar = cfg.ladybug_path.with_suffix(".lbug.snapshot")
    warm.begin_graph_snapshot()
    assert sidecar.is_file(), "precondition: sidecar should exist after begin"

    # Write the original via an incremental rebuild.
    (corpus / _NEW_JAVA_REL).parent.mkdir(parents=True, exist_ok=True)
    (corpus / _NEW_JAVA_REL).write_text(_NEW_JAVA_CONTENT, encoding="utf-8")
    inc_proc = run_incremental_graph(
        source_root=corpus,
        ladybug_path=cfg.ladybug_path,
        verbose=True,
        quiet=False,
        env={
            **os.environ,
            "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
            "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
        },
    )
    assert inc_proc.returncode == 0, f"run_incremental_graph failed: {inc_proc.stderr}"

    warm.commit_graph_snapshot()

    assert not sidecar.exists(), f"sidecar still present after commit: {sidecar}"
    reopened = warm.graph()
    assert reopened.db_path == str(cfg.ladybug_path), (
        f"after commit, graph().db_path={reopened.db_path!r} != original "
        f"{str(cfg.ladybug_path)!r}"
    )
    reopened_count = _graph_symbol_count(Path(reopened.db_path))
    assert reopened_count > pre_count, (
        f"graph() did not reopen the updated original after commit "
        f"(pre={pre_count}, reopened={reopened_count})"
    )


# ------------------------------------------- (e) reset_for_path -----------------


def test_reset_for_path_clears_singleton(tmp_path: Path, monkeypatch) -> None:
    """``reset_for_path`` clears the cached singleton on None / matching path only.

    Inputs: a built graph at ``cfg.ladybug_path``; an opened singleton via ``get``.
    Expected:
      * ``reset_for_path(None)`` clears the singleton; the next ``get`` reopens a
        NEW instance (different identity).
      * after reopening, ``reset_for_path(<non-matching path>)`` is a NO-OP (the
        singleton is untouched), while ``reset_for_path(<current path>)`` clears it.
    """
    from java_codebase_rag.config import resolve_operator_config
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph

    corpus, index_dir = _sandbox(tmp_path, "reset")
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir.resolve()))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus.resolve()))
    cfg = resolve_operator_config(source_root=corpus)
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    _build_graph(corpus, cfg.ladybug_path)
    db_path = str(cfg.ladybug_path)

    g1 = LadybugGraph.get(db_path)
    assert LadybugGraph._instance is g1, "precondition: get() cached the instance"

    # (1) reset_for_path(None) clears the singleton.
    LadybugGraph.reset_for_path(None)
    assert LadybugGraph._instance is None, "reset_for_path(None) did not clear _instance"
    assert LadybugGraph._instance_path is None, "reset_for_path(None) did not clear _instance_path"

    # Next get() reopens a different instance.
    g2 = LadybugGraph.get(db_path)
    assert g2 is not g1, "get() returned the old instance after reset_for_path(None)"
    assert LadybugGraph._instance is g2

    # (2) reset_for_path(<non-matching path>) is a no-op.
    LadybugGraph.reset_for_path(str(cfg.ladybug_path.parent / "code_graph.OTHER.lbug"))
    assert LadybugGraph._instance is g2, "non-matching reset_for_path wrongly cleared singleton"

    # (3) reset_for_path(<current path>) clears it.
    LadybugGraph.reset_for_path(db_path)
    assert LadybugGraph._instance is None, "reset_for_path(current) did not clear _instance"
    assert LadybugGraph._instance_path is None
