"""PR-T1: Foundation tests — determinism, .deps.json read/write/validate."""

from __future__ import annotations

import json
from pathlib import Path

from build_ast_graph import (
    ONTOLOGY_VERSION,
    FileDeps,
    _read_dependency_index,
    pass1_parse,
    pass2_edges,
    pass3_calls,
    pass4_routes,
    pass5_imperative_edges,
    pass6_match_edges,
    write_kuzu,
)

CORPUS = Path(__file__).resolve().parent / "bank-chat-system"


def _full_rebuild_into(corpus: Path, db_path: Path) -> Path:
    """Run pass1–6 + write_kuzu into db_path; return db_path."""
    from build_ast_graph import GraphTables

    tables = GraphTables()
    asts = pass1_parse(corpus, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    pass4_routes(tables, asts, source_root=corpus, verbose=False)
    pass5_imperative_edges(tables, asts, source_root=corpus, verbose=False)
    pass6_match_edges(tables, verbose=False)
    write_kuzu(db_path, tables, source_root=corpus, verbose=False)
    return db_path


def _dump_node_ids(db_path: Path) -> set[str]:
    """Return all Symbol node IDs from a Kuzu database."""
    import kuzu

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    result = conn.execute("MATCH (s:Symbol) RETURN s.id AS id")
    ids = set()
    while result.has_next():
        ids.add(result.get_next()[0])
    conn.close()
    return ids


def _dump_edge_rows(db_path: Path) -> set[tuple[str, ...]]:
    """Return edge tuples (src, dst, label) from all relationship tables."""
    import kuzu

    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    labels = [
        "DECLARES", "EXTENDS", "IMPLEMENTS", "INJECTS",
        "CALLS", "OVERRIDES", "EXPOSES",
        "DECLARES_CLIENT", "DECLARES_PRODUCER",
        "HTTP_CALLS", "ASYNC_CALLS",
    ]
    rows: set[tuple[str, ...]] = set()
    for label in labels:
        try:
            result = conn.execute(
                f"MATCH (a)-[e:{label}]->(b) RETURN a.id AS src, b.id AS dst"
            )
        except Exception:
            continue
        while result.has_next():
            row = result.get_next()
            rows.add((row[0], row[1], label))
    conn.close()
    return rows


# ---- PR-T1 tests ----


def test_full_rebuild_is_deterministic(tmp_path: Path) -> None:
    """Two full rebuilds on the same corpus produce identical graph state."""
    db_a = tmp_path / "a" / "code_graph.kuzu"
    db_b = tmp_path / "b" / "code_graph.kuzu"
    _full_rebuild_into(CORPUS, db_a)
    _full_rebuild_into(CORPUS, db_b)

    nodes_a = _dump_node_ids(db_a)
    nodes_b = _dump_node_ids(db_b)
    assert nodes_a == nodes_b, (
        f"Node ID sets differ: {len(nodes_a)} vs {len(nodes_b)}"
    )

    edges_a = _dump_edge_rows(db_a)
    edges_b = _dump_edge_rows(db_b)
    assert edges_a == edges_b, (
        f"Edge sets differ: {len(edges_a)} vs {len(edges_b)}"
    )


def test_deps_json_written_on_full_rebuild(tmp_path: Path) -> None:
    """After a full rebuild, .deps.json exists and is well-formed."""
    db_path = tmp_path / "code_graph.kuzu"
    _full_rebuild_into(CORPUS, db_path)

    deps_path = db_path.parent / ".deps.json"
    assert deps_path.is_file(), ".deps.json missing after full rebuild"

    data = json.loads(deps_path.read_text())
    assert data["version"] == 1
    assert data["ontology_version"] == ONTOLOGY_VERSION
    assert isinstance(data["files"], dict)
    assert len(data["files"]) > 0


def test_deps_json_fields_coverage(tmp_path: Path) -> None:
    """Spot-check a known file has expected dependency entries."""
    db_path = tmp_path / "code_graph.kuzu"
    _full_rebuild_into(CORPUS, db_path)
    deps_path = db_path.parent / ".deps.json"

    idx = _read_dependency_index(deps_path)
    assert idx is not None

    # Find ChatIngressController.java — it should have declares, uses_anno, etc.
    ctrl_files = [fp for fp in idx.files if "ChatIngressController" in fp]
    assert len(ctrl_files) >= 1, "ChatIngressController.java not found in deps index"

    fp = ctrl_files[0]
    deps = idx.files[fp]
    assert isinstance(deps, FileDeps)
    assert deps.ext_hash, "ext_hash should be non-empty"
    assert len(deps.declares) >= 1, "ChatIngressController should declare at least one type"
    assert any(
        "Controller" in a or "Mapping" in a
        for a in deps.uses_anno
    ), f"Expected controller/mapping annotations, got {deps.uses_anno}"


def test_deps_json_stale_detection(tmp_path: Path) -> None:
    """_read_dependency_index returns None for stale ontology version."""
    deps_path = tmp_path / ".deps.json"
    stale = {
        "version": 1,
        "ontology_version": 0,  # intentionally wrong
        "files": {},
    }
    deps_path.write_text(json.dumps(stale))
    assert _read_dependency_index(deps_path) is None


def test_deps_json_missing_returns_none(tmp_path: Path) -> None:
    """_read_dependency_index returns None when file doesn't exist."""
    assert _read_dependency_index(tmp_path / "nonexistent.json") is None
