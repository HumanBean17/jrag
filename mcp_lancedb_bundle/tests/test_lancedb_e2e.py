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
pytestmark = pytest.mark.skipif(
    not HEAVY,
    reason="set LANCEDB_MCP_RUN_HEAVY=1 to run the cocoindex + LanceDB end-to-end test",
)


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
    bundle_dir = Path(__file__).resolve().parent.parent
    cocoindex_bin = Path(sys.executable).resolve().parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(f"cocoindex CLI not found next to Python ({cocoindex_bin})")

    work = tmp_path_factory.mktemp("lance_e2e")
    lance_uri = work / "lancedb_data"
    coco_db = work / "cocoindex.db"

    # cocoindex walks the *current working directory*, so we hand it the
    # corpus root rather than the bundle dir.
    flow_path = bundle_dir / "java_index_flow_lancedb.py"
    assert flow_path.is_file(), flow_path

    env = {
        **os.environ,
        "LANCEDB_URI": str(lance_uri),
        "COCOINDEX_DB": str(coco_db),
    }
    proc = subprocess.run(
        [
            str(cocoindex_bin),
            "update",
            f"{flow_path.name}:JavaCodeIndexLance",
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
