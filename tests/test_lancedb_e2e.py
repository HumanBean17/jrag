"""End-to-end test: real LanceDB index + AST graph + MCP search tools.

Gated behind ``LANCEDB_MCP_RUN_HEAVY=1`` because this test:

* runs ``cocoindex update`` against the bank-chat-system corpus, which
  downloads the embedding model on first use,
* writes a real LanceDB directory under ``tmp_path``,
* and only then invokes ``codebase_search`` / ``trace_flow``.

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

HEAVY = os.environ.get("LANCEDB_MCP_RUN_HEAVY", "").strip().lower() in ("1", "true", "yes")
pytestmark = [
    pytest.mark.skipif(
        not HEAVY,
        reason="set LANCEDB_MCP_RUN_HEAVY=1 to run the cocoindex + LanceDB end-to-end test",
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
    """Build ``path:JavaCodeIndexLance`` for ``cocoindex update`` with ``cwd=index_cwd``.

    A bare ``java_index_flow_lancedb.py`` is resolved with `os.path.isfile` against
    *only* the current working directory, so the flow file in ``bundle_dir/`` is not
    found when we index from a corpus (or any other) directory. A **relative** path
    from ``index_cwd`` to the real file fixes that. We avoid
    ``C:\\...\\x.py:App`` on Windows (``:`` in the app specifier breaks parsing).
    """
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
    # Do not ``Path(sys.executable).resolve()`` — on macOS the venv ``python`` is a
    # symlink; resolving it lands in ``.../Python.framework/.../bin`` and we would
    # pick the wrong ``cocoindex`` (system site-packages, missing ``tree_sitter_java``).
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter; install cocoindex in this "
            f"venv and run: `.venv/bin/python -m pytest ...` ({cocoindex_bin})"
        )

    work = tmp_path_factory.mktemp("lance_e2e")
    lance_uri = work / "lancedb_data"
    coco_db = work / "cocoindex.db"

    # cocoindex walks the *current working directory*, so we hand it the
    # corpus root rather than the bundle dir. The app module path must be
    # resolvable from that cwd (see _cocoindex_flow_specifier).
    app_spec = _cocoindex_flow_specifier(bundle_dir, Path(corpus_root))

    env = {
        **os.environ,
        "LANCEDB_URI": str(lance_uri),
        "COCOINDEX_DB": str(coco_db),
        "LANCEDB_MCP_PROJECT_ROOT": str(Path(corpus_root).resolve()),
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

    # Builder for the Kuzu graph that lives next to the Lance index.
    builder = bundle_dir / "build_ast_graph.py"
    proc = subprocess.run(
        [sys.executable, str(builder), "--source-root", str(corpus_root)],
        env={**env, "KUZU_DB_PATH": str(lance_uri / "code_graph.kuzu")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return lance_uri


@pytest.fixture(scope="module")
def lance_index_capability_smoke(tmp_path_factory) -> Path:
    """Tiny project with @KafkaListener — indexes fast; tests `capability=` in search."""
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
    lance_uri = work / "lancedb_data"
    coco_db = work / "cocoindex.db"
    app_spec = _cocoindex_flow_specifier(bundle_dir, Path(CAPABILITY_SMOKE_ROOT))
    env = {
        **os.environ,
        "LANCEDB_URI": str(lance_uri),
        "COCOINDEX_DB": str(coco_db),
        "LANCEDB_MCP_PROJECT_ROOT": str(CAPABILITY_SMOKE_ROOT.resolve()),
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
        [sys.executable, str(builder), "--source-root", str(CAPABILITY_SMOKE_ROOT)],
        env={**env, "KUZU_DB_PATH": str(lance_uri / "code_graph.kuzu")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return lance_uri


async def test_codebase_search_returns_hits(lance_index: Path, monkeypatch) -> None:
    monkeypatch.setenv("LANCEDB_URI", str(lance_index))
    monkeypatch.setenv("KUZU_DB_PATH", str(lance_index / "code_graph.kuzu"))

    # Reset the singletons so the env switch takes effect.
    from kuzu_queries import KuzuGraph
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None

    from server import create_mcp_server
    server = create_mcp_server()

    out = _structured(
        await server.call_tool(
            "codebase_search",
            {"query": "how chat assigns operator on incoming message", "limit": 5},
        )
    )
    assert out["success"] is True
    assert out["results"], out
    # Loose contract: every hit has a file path inside the corpus.
    for hit in out["results"]:
        assert hit["file_path"]


async def test_codebase_search_capability_filter_e2e(
    lance_index_capability_smoke: Path, monkeypatch,
) -> None:
    """MCP `codebase_search` with `capability` — full Lance + enrich path (heavy)."""
    monkeypatch.setenv("LANCEDB_URI", str(lance_index_capability_smoke))
    monkeypatch.setenv("KUZU_DB_PATH", str(lance_index_capability_smoke / "code_graph.kuzu"))

    from kuzu_queries import KuzuGraph
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None

    from server import create_mcp_server
    server = create_mcp_server()

    out = _structured(
        await server.call_tool(
            "codebase_search",
            {
                "query": "kafka listener consumer message handler",
                "limit": 10,
                "capability": "MESSAGE_LISTENER",
            },
        )
    )
    assert out["success"] is True
    assert out["results"], out
    caps_any = any(
        (h.get("capabilities") or []) for h in out["results"]
    )
    assert caps_any, "expected at least one hit with non-empty capabilities from smoke index"
    assert any(
        "MESSAGE_LISTENER" in (h.get("capabilities") or [])
        for h in out["results"]
    ), out["results"]


def _unique_java_filenames_in_lance(lance_uri: Path) -> int:
    import lancedb

    db = lancedb.connect(str(lance_uri))
    tbl = db.open_table("javacodeindex_java_code")
    names = tbl.to_arrow().column("filename").to_pylist()
    return len(set(names))


def test_lancedb_ignore_file_reduces_indexed_java_files(tmp_path_factory) -> None:
    """PR-C test 47: ``.lancedb-mcp/ignore`` excludes generated sources from the Lance index."""
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
    shutil.rmtree(without_dir / ".lancedb-mcp")

    def run_coco(corpus: Path) -> Path:
        lance_uri = corpus / "lancedb_data"
        coco_db = corpus / "cocoindex.db"
        app_spec = _cocoindex_flow_specifier(bundle_dir, corpus)
        env = {
            **os.environ,
            "LANCEDB_URI": str(lance_uri.resolve()),
            "COCOINDEX_DB": str(coco_db.resolve()),
            "LANCEDB_MCP_PROJECT_ROOT": str(corpus.resolve()),
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
        return lance_uri

    n_with = _unique_java_filenames_in_lance(run_coco(with_dir))
    n_without = _unique_java_filenames_in_lance(run_coco(without_dir))
    assert n_without > n_with
    assert n_with >= 1


async def test_trace_flow_returns_stages(lance_index: Path, monkeypatch) -> None:
    monkeypatch.setenv("LANCEDB_URI", str(lance_index))
    monkeypatch.setenv("KUZU_DB_PATH", str(lance_index / "code_graph.kuzu"))

    from kuzu_queries import KuzuGraph
    KuzuGraph._instance = None
    KuzuGraph._instance_path = None

    from server import create_mcp_server
    server = create_mcp_server()

    out = _structured(
        await server.call_tool(
            "trace_flow",
            {"query": "what happens when a chat is assigned to an operator"},
        )
    )
    assert out["success"] is True
    assert out["stages"], out
    # Stage 0 should contain at least one entrypoint-role symbol.
    stage0 = out["stages"][0]
    assert any(
        s["symbol"]["role"] in {"CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"}
        for s in stage0["symbols"]
    )
