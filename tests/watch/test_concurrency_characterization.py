"""Characterization tests for the ``jrag watch`` daemon's three concurrency pillars.

Gated behind ``JAVA_CODEBASE_RAG_RUN_HEAVY=1`` because every test builds a real
Lance + Ladybug index via the production pipeline (cocoindex +
``build_ast_graph.py``), which downloads the embedding model on first use.

These are CHARACTERIZATION tests, not feature tests: they assert what the
installed engines ACTUALLY do. The daemon's design (see
``docs/specs/2026-07-11-watch-mode-design.md`` §4.7/§9) rests on three pillars,
locked in here so that Tasks 6 (warm resources) and 9 (reindex watcher) can rely
on them. Where reality diverges from the design's expectation, the divergence is
recorded in the test's docstring and the assertion tracks reality — we never
silently change engine behavior.

The three pillars:

(a) **Lance atomic versions** — Lance commits are atomic per version. A fresh
    ``lancedb.connect`` per query returns a consistent old-or-new view during/after
    a cocoindex write, never partial, never blocked. (``test_lance_atomic_versions``)

(b) **Ladybug graph COW snapshot isolation** — the ``ladybug`` package (a kùzu
    wrapper) has no transaction API and is single-writer. Graph builds run as
    SUBPROCESSES (``run_incremental_graph``). A read-only reader on a file COPY
    (sidecar) keeps returning pre-write data while the subprocess writes the
    ORIGINAL, and the two never conflict (different files). This is the
    load-bearing finding: it is why the daemon can serve graph reads during a
    reindex by copying ``code_graph.lbug`` to a sidecar. (``test_graph_cow_snapshot``)

(c) **Warm model reuse** — ``mcp_v2._get_sentence_transformer`` caches a single
    ``SentenceTransformer`` in a module global and reuses it across calls in one
    process (the entire warmth win). (``test_warm_model_reuse``)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# --- Heavy gate (mirrors tests/integration/test_lancedb_e2e.py) -----------------
HEAVY = (
    os.environ.get("JAVA_CODEBASE_RAG_RUN_HEAVY", "").strip().lower() in ("1", "true", "yes")
)
pytestmark = pytest.mark.skipif(
    not HEAVY,
    reason="set JAVA_CODEBASE_RAG_RUN_HEAVY=1 to run the watch-mode concurrency "
    "characterization tests (build a real Lance + Ladybug index via cocoindex)",
)

_TESTS_DIR = Path(__file__).resolve().parent.parent
# Small Maven-layout corpus (16 .java files) — indexes in seconds once the
# embedding model is cached. Copied per test so each can mutate (add a file).
_FIXTURE_CORPUS = _TESTS_DIR / "fixtures" / "call_graph_smoke"
_LANCE_TABLE = "javacodeindex_java_code"

# A minimal new Java source added between the initial build and the catch-up /
# incremental rebuild. Stored in Lance under this same relative path (the table's
# ``filename`` column holds repo-relative paths), so membership is checked by full
# path, not basename.
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


# --- helpers ------------------------------------------------------------------


def _require_pipeline_deps() -> None:
    """Skip if the cocoindex binary or tree-sitter-java are absent (graph-only env)."""
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            "Heavy characterization needs project deps in the current env "
            f"(pip install -e .[dev]): {exc}"
        )
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(f"cocoindex CLI not found next to the pytest interpreter ({cocoindex_bin})")


def _sandbox(tmp_path: Path, tag: str) -> tuple[Path, Path, dict[str, str]]:
    """Copy the fixture corpus into a temp dir and return (corpus, index_dir, env).

    Each test gets an isolated, mutable corpus copy and a fresh index dir so the
    initial build + the catch-up/incremental run see a clean state.
    """
    _require_pipeline_deps()
    assert _FIXTURE_CORPUS.is_dir(), f"fixture corpus missing: {_FIXTURE_CORPUS}"
    corpus = tmp_path / f"corpus_{tag}"
    shutil.copytree(_FIXTURE_CORPUS, corpus)
    index_dir = tmp_path / f"index_{tag}" / ".java-codebase-rag"
    index_dir.mkdir(parents=True)
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(corpus.resolve()),
    }
    return corpus, index_dir, env


def _graph_symbol_count(ladybug_path: Path) -> int:
    """Ground-truth count of ``:Symbol`` nodes via a fresh read-only connection.

    ``LadybugGraph.meta()["counts"]`` is NOT a reliable node count across an
    incremental rebuild: when a newly-added file resolves phantom nodes, the
    ``phantoms`` count drops while ``types``/``members`` rise, so the summed node
    total can stay flat (observed: 88 -> 88) even though the real ``:Symbol``
    count grew (88 -> 93). The direct query is the reliable measure and is what
    the COW isolation assertion uses.
    """
    import ladybug

    conn = ladybug.Connection(ladybug.Database(str(ladybug_path), read_only=True))
    result = conn.execute("MATCH (s:Symbol) RETURN count(*) AS n")
    return int(result.get_next()[0]) if result.has_next() else 0


# ---------------------------------------------------------------- (a) Lance ----


def test_lance_atomic_versions(tmp_path: Path) -> None:
    """Lance commits are atomic per version; fresh connects see old-or-new, never partial.

    Setup: build vectors over the fixture corpus, capture (1) the table version and
    row/filename snapshot via a handle opened BEFORE the write, then add a new
    ``.java`` file and run a cocoindex catch-up. Open a FRESH connect after the
    write and compare, then re-read the pre-write handle.

    Findings locked in (assert):
      * ``table.version`` is an int, and the fresh post-write version is strictly
        greater than the pre-write version. (The catch-up plus the serialized
        optimize commit SEVERAL versions, not exactly one — observed delta is
        typically +4. The pillar only requires monotonic, atomic version bumps, so
        we assert strict growth and document the real delta.)
      * The fresh post-write table contains the new file's chunks (by full path);
        the pre-write snapshot did not. (Atomic committed read — the whole file
        appears at once, never a partial row set.)
      * The fresh post-write row count is strictly greater than the pre-write count.
      * The already-open (pre-write) handle reports a consistent snapshot after the
        commit — exactly the pre-write row set, never a partial mix. (Lance pins an
        open table object to the version it was opened at; the daemon nonetheless
        connects fresh per query, so this is an extra guarantee, not a dependency.)
    """
    import lancedb

    from java_codebase_rag.pipeline import run_cocoindex_update

    corpus, index_dir, env = _sandbox(tmp_path, "lance")

    # Initial vectors build (same path `init` takes: full_reprocess=False on a
    # fresh index dir creates the tables and establishes the cocoindex memo).
    proc = run_cocoindex_update(env, full_reprocess=False, quiet=True, verbose=False)
    assert proc.returncode == 0, f"initial cocoindex failed: {proc.stderr}"

    # --- pre-write snapshot via a handle opened BEFORE the catch-up ---
    stale_db = lancedb.connect(str(index_dir))
    stale_tbl = stale_db.open_table(_LANCE_TABLE)
    pre_version = stale_tbl.version
    pre_rows = stale_tbl.to_arrow().num_rows
    pre_filenames = set(stale_tbl.to_arrow().column("filename").to_pylist())
    assert isinstance(pre_version, int), f"version not int: {pre_version!r}"
    assert _NEW_JAVA_REL not in pre_filenames, (
        "fixture corpus unexpectedly already contains the watch test file"
    )

    # --- add a new .java file and run the catch-up (incremental cocoindex) ---
    (corpus / _NEW_JAVA_REL).parent.mkdir(parents=True, exist_ok=True)
    (corpus / _NEW_JAVA_REL).write_text(_NEW_JAVA_CONTENT, encoding="utf-8")
    proc = run_cocoindex_update(env, full_reprocess=False, quiet=True, verbose=False)
    assert proc.returncode == 0, f"catch-up cocoindex failed: {proc.stderr}"

    # --- fresh handle AFTER the write ---
    fresh_db = lancedb.connect(str(index_dir))
    fresh_tbl = fresh_db.open_table(_LANCE_TABLE)
    post_version = fresh_tbl.version
    post_rows = fresh_tbl.to_arrow().num_rows
    post_filenames = set(fresh_tbl.to_arrow().column("filename").to_pylist())
    assert isinstance(post_version, int), f"post version not int: {post_version!r}"

    # The catch-up committed newer version(s): the fresh connect sees a strictly
    # newer atomic version with the new file fully present (by full path).
    assert post_version > pre_version, (
        f"expected post_version > pre_version; got pre={pre_version} post={post_version}"
    )
    assert _NEW_JAVA_REL in post_filenames, (
        "fresh post-write table missing the new file's chunks — atomic read failed"
    )
    assert post_rows > pre_rows, (
        f"expected post_rows > pre_rows; got pre={pre_rows} post={post_rows}"
    )

    # --- characterize the already-open (pre-write) handle's view after the commit ---
    stale_rows_after = stale_tbl.to_arrow().num_rows
    stale_filenames_after = set(stale_tbl.to_arrow().column("filename").to_pylist())
    # Lance version isolation: an already-open table object reports a consistent
    # snapshot — exactly the pre-write OR exactly the post-write row set, never a
    # partial mix. (Empirically it pins to the pre-write set; we accept either
    # consistent outcome and assert the new file is absent iff the count is pre.)
    assert stale_rows_after in (pre_rows, post_rows), (
        "pre-write handle returned a partial row set after the commit "
        f"(pre={pre_rows}, post={post_rows}, stale_after={stale_rows_after})"
    )
    if stale_rows_after == pre_rows:
        assert _NEW_JAVA_REL not in stale_filenames_after, (
            "pre-write handle pinned the old row count but shows the new file — inconsistent"
        )


# --------------------------------------------------------- (b) graph COW -------


def test_graph_cow_snapshot(tmp_path: Path) -> None:
    """A read-only reader on a graph file COPY is isolated from a subprocess write to the original.

    This is the load-bearing finding for the daemon's graph-read-during-reindex
    strategy (design §4.7). The ``ladybug`` package has no transaction API and a
    ``Database`` is single-writer, so graph builds run as a SUBPROCESS that owns the
    original file. To serve graph reads during a write, the daemon will copy
    ``code_graph.lbug`` to a sidecar and read the sidecar while the subprocess
    writes the original.

    Setup: build a graph at ``ladybug_path``, snapshot its ``:Symbol`` count, copy
    the file to a sidecar and open a read-only reader on the sidecar. Then, WITH THE
    SIDECAR READER STILL OPEN, add a ``.java`` file and run ``run_incremental_graph``
    against the ORIGINAL path.

    Findings locked in (assert):
      * The sidecar reader's symbol count equals the original's pre-write count.
      * The incremental subprocess writing the original SUCCEEDS (returncode 0)
        while the sidecar reader is open — no single-writer conflict, because the
        two touch different files. (If this fails, the daemon's COW strategy is
        invalid and later tasks must change — report BLOCKED.)
      * After the subprocess completes, the sidecar reader STILL returns the
        PRE-write count (the copy is unaffected by the write to the original).
      * A fresh reader on the ORIGINAL returns the POST-write count (strictly
        greater), proving the write landed on the original only.

    Implementation notes:
      * Node count is measured by a direct ``MATCH (s:Symbol) RETURN count(*)``
        query, NOT ``LadybugGraph.meta()["counts"]`` — see ``_graph_symbol_count``.
      * ``LadybugGraph.reset_for_path`` does not exist yet (Task 6 adds it); we
        reset the singleton manually here (``_instance``/``_instance_path = None``),
        exactly as the existing heavy e2e tests do.
      * ``code_graph.lbug`` is a single file (not a directory), so ``shutil.copy2``
        is the correct snapshot operation.
    """
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph
    from java_codebase_rag.pipeline import run_build_ast_graph, run_incremental_graph

    corpus, index_dir, env = _sandbox(tmp_path, "graph")
    ladybug_path = index_dir / "code_graph.lbug"

    # Initial full graph build (subprocess — writes code_graph.lbug + .graph_hashes.json,
    # so the subsequent incremental rebuild can detect the newly-added file).
    proc = run_build_ast_graph(
        source_root=corpus,
        ladybug_path=ladybug_path,
        verbose=False,
        quiet=True,
        env=env,
    )
    assert proc.returncode == 0, f"initial graph build failed: {proc.stderr}"

    pre_count = _graph_symbol_count(ladybug_path)
    assert pre_count > 0, "initial graph has no Symbol nodes"

    # --- COW: copy the file to a sidecar and open a read-only reader on it ---
    sidecar = ladybug_path.with_suffix(".lbug.snapshot")
    assert ladybug_path.is_file(), f"code_graph.lbug is not a file: {ladybug_path}"
    shutil.copy2(ladybug_path, sidecar)
    assert sidecar.is_file(), f"sidecar copy failed: {sidecar}"

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    # Open a persistent read-only reader on the sidecar. It stays open for the
    # whole subprocess write below — proving a live reader on the COPY does not
    # block the writer on the ORIGINAL (different files, no single-writer clash).
    sidecar_graph = LadybugGraph.get(str(sidecar))
    assert sidecar_graph.db_path == str(sidecar)
    sidecar_pre_count = _graph_symbol_count(sidecar)
    assert sidecar_pre_count == pre_count, (
        f"sidecar symbol count != original pre-write count "
        f"({sidecar_pre_count} vs {pre_count})"
    )

    # --- add a .java file and run the incremental rebuild against the ORIGINAL,
    #     with the sidecar reader still open ---
    (corpus / _NEW_JAVA_REL).parent.mkdir(parents=True, exist_ok=True)
    (corpus / _NEW_JAVA_REL).write_text(_NEW_JAVA_CONTENT, encoding="utf-8")
    inc_proc = run_incremental_graph(
        source_root=corpus,
        ladybug_path=ladybug_path,
        verbose=True,
        quiet=False,
        env=env,
    )
    assert inc_proc.returncode == 0, (
        f"run_incremental_graph failed while sidecar reader was open: {inc_proc.stderr}"
    )

    # --- the sidecar reader is unaffected: still the pre-write count ---
    sidecar_post_count = _graph_symbol_count(sidecar)
    assert sidecar_post_count == pre_count, (
        "sidecar reader observed a change after the original was written — COW "
        f"isolation broken (sidecar pre={sidecar_pre_count}, after={sidecar_post_count})"
    )

    # --- a fresh reader on the ORIGINAL sees the post-write count ---
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    post_count = _graph_symbol_count(ladybug_path)
    assert post_count > pre_count, (
        f"original symbol count did not grow after incremental rebuild "
        f"(pre={pre_count}, post={post_count}); the new file may not have indexed"
    )

    # Final isolation invariant: sidecar (old) and original (new) now differ.
    assert sidecar_post_count < post_count


# ------------------------------------------------------- (c) warm model --------


def test_warm_model_reuse(tmp_path: Path, monkeypatch) -> None:
    """The embedding model is loaded once per process and reused (the warmth win).

    ``mcp_v2._get_sentence_transformer(model_name, device)`` caches a single
    ``SentenceTransformer`` in the module global ``_st_model``. Two calls in one
    process return the very same object (identical ``id`` / ``is``), and
    ``search_v2`` reuses that same instance on its vector branch.
    """
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.pipeline import run_build_ast_graph, run_cocoindex_update
    from java_codebase_rag.search.index_common import SBERT_MODEL

    # Isolate the module-global cache for this test.
    mcp_v2._st_model = None

    device = os.environ.get("SBERT_DEVICE") or None
    m1 = mcp_v2._get_sentence_transformer(SBERT_MODEL, device)
    m2 = mcp_v2._get_sentence_transformer(SBERT_MODEL, device)
    # Singleton: same object, same id.
    assert m1 is m2, "two _get_sentence_transformer calls returned different objects"
    assert id(m1) == id(m2), f"id mismatch: {id(m1)} vs {id(m2)}"

    # Drive search_v2 once on a real index so its vector branch invokes
    # _get_sentence_transformer; the cached instance must survive the call.
    corpus, index_dir, env = _sandbox(tmp_path, "warm")
    proc = run_cocoindex_update(env, full_reprocess=False, quiet=True, verbose=False)
    assert proc.returncode == 0, f"cocoindex failed: {proc.stderr}"
    ladybug_path = index_dir / "code_graph.lbug"
    proc = run_build_ast_graph(
        source_root=corpus,
        ladybug_path=ladybug_path,
        verbose=False,
        quiet=True,
        env=env,
    )
    assert proc.returncode == 0, f"graph build failed: {proc.stderr}"

    # Point the readers the search path uses at this temp index (scoped to the test).
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(index_dir.resolve()))
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus.resolve()))
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph

    LadybugGraph._instance = None
    LadybugGraph._instance_path = None

    out = mcp_v2.search_v2("class that greets", table="java", limit=5)
    # Whether or not it ranked a hit, the call must have run on the vector branch
    # without replacing the cached model.
    assert mcp_v2._st_model is m1, (
        "search_v2 replaced the cached SentenceTransformer — model is not reused"
    )
    # And a subsequent getter still returns the same instance.
    m3 = mcp_v2._get_sentence_transformer(SBERT_MODEL, device)
    assert m3 is m1, "model not reused across search_v2 and a later getter call"
    # Sanity: search_v2 actually executed and returned a result object.
    assert hasattr(out, "success")
