"""Tests for `build_ast_graph.py` against the bank-chat-system corpus.

These tests pin *structural* invariants of the build (schema present, every
edge type populated, service inference works for both single-module and
multi-module Maven projects). They intentionally avoid asserting on exact
node / edge counts — those will drift as the fixture grows. See
`tests/README.md` for the anti-overfitting rules.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import kuzu
import pytest

from ast_java import ONTOLOGY_VERSION


def _connect(db_path: Path) -> kuzu.Connection:
    db = kuzu.Database(str(db_path), read_only=True)
    return kuzu.Connection(db)


def _scalar(conn: kuzu.Connection, query: str) -> int:
    r = conn.execute(query)
    if not r.has_next():
        return 0
    return int(r.get_next()[0] or 0)


def _column(conn: kuzu.Connection, query: str, idx: int = 0) -> list:
    r = conn.execute(query)
    out: list = []
    while r.has_next():
        out.append(r.get_next()[idx])
    return out


def test_kuzu_db_directory_exists(kuzu_db_path: Path) -> None:
    assert kuzu_db_path.exists()


def test_schema_has_all_expected_tables(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    # `CALL show_tables() RETURN *;` returns rows (id, name, type, ...) — name is at index 1.
    tables = set(_column(conn, "CALL show_tables() RETURN *;", idx=1))
    # We only assert the tables we depend on are present. The builder is
    # free to add more (e.g. CALLS later) without breaking this test.
    expected = {
        "Symbol", "Route", "Client", "GraphMeta",
        "EXTENDS", "IMPLEMENTS", "INJECTS", "DECLARES", "OVERRIDES", "CALLS", "EXPOSES", "DECLARES_CLIENT",
    }
    missing = expected - tables
    assert not missing, f"missing schema tables: {missing}; saw {tables}"


def test_graph_meta_present_and_versioned(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    r = conn.execute(
        "MATCH (m:GraphMeta) RETURN m.ontology_version, m.built_at, "
        "m.source_root, m.parse_errors, m.counts_json, "
        "m.routes_total, m.exposes_total, m.routes_by_framework, m.routes_resolved_pct, "
        "m.routes_from_brownfield_pct, m.routes_by_layer"
    )
    rows: list = []
    while r.has_next():
        rows.append(r.get_next())
    assert len(rows) == 1, "expected exactly one GraphMeta row"
    row = rows[0]
    ov = row[0]
    built_at = row[1]
    source_root = row[2]
    parse_errors = row[3]
    counts_json = row[4]
    routes_total = row[5]
    exposes_total = row[6]
    routes_by_framework_raw = row[7]
    routes_resolved_pct = row[8]
    routes_from_brownfield_pct = row[9]
    routes_by_layer_raw = row[10]
    assert int(ov) == ONTOLOGY_VERSION
    assert int(built_at) > 0
    assert source_root  # absolute path string
    # Parse errors should be tolerable on a clean fixture; this catches
    # accidental tree-sitter regressions that break every file at once.
    assert int(parse_errors) <= 0  # bank-chat-system is hand-written, no errors expected
    assert counts_json and counts_json.startswith("{")
    counts = json.loads(counts_json)
    assert counts.get("routes", 0) >= 1
    assert int(routes_total) >= 1
    assert int(exposes_total) >= 1
    assert float(routes_resolved_pct) >= 0.0
    by_fw = json.loads(routes_by_framework_raw)
    assert isinstance(by_fw, dict)
    assert len(by_fw) >= 1
    assert float(routes_from_brownfield_pct) >= 0.0
    by_layer = json.loads(routes_by_layer_raw)
    assert isinstance(by_layer, dict)


def test_each_node_kind_present(kuzu_db_path: Path) -> None:
    """Builder must emit at least one node of every Phase-1 kind we care about.

    Exact counts are a moving target; non-zero is the meaningful invariant.
    """
    conn = _connect(kuzu_db_path)
    kinds = set(_column(conn, "MATCH (s:Symbol) RETURN DISTINCT s.kind"))
    for required in ("package", "file", "class", "interface", "method", "constructor"):
        assert required in kinds, f"missing node kind: {required}; saw {kinds}"


def test_each_edge_type_populated(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    assert _scalar(conn, "MATCH ()-[e:EXTENDS]->() RETURN count(e)") > 0
    assert _scalar(conn, "MATCH ()-[e:IMPLEMENTS]->() RETURN count(e)") > 0
    assert _scalar(conn, "MATCH ()-[e:INJECTS]->() RETURN count(e)") > 0


def test_calls_and_declares_edges_populated(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    assert _scalar(conn, "MATCH ()-[e:CALLS]->() RETURN count(e)") > 0
    assert _scalar(conn, "MATCH ()-[e:DECLARES]->() RETURN count(e)") > 0


def test_module_inference_recognises_both_layouts(kuzu_graph) -> None:
    """`module_for_path` must find each Maven module's name.

    The corpus exercises both a single-module project (chat-assign) and a
    multi-module reactor (chat-core/{chat-app,chat-engine,chat-domain,
    chat-contracts}). The MCP must handle both shapes.
    """
    counts = kuzu_graph.module_counts()
    # Defensive: don't pin every module to >0 in case the corpus is
    # trimmed; instead require both styles to be represented.
    assert counts.get("chat-assign", 0) > 0, counts
    multi_module = {"chat-app", "chat-engine", "chat-domain", "chat-contracts"}
    seen = multi_module & set(counts)
    assert len(seen) >= 2, (
        "expected module inference to surface multiple chat-core child "
        f"modules, got: {sorted(set(counts))}"
    )


def test_microservice_inference_groups_multi_module_reactor(kuzu_graph) -> None:
    """Multi-module reactor child modules must collapse to one microservice key.

    `chat-core` is the outermost build-marker ancestor for every
    `chat-core/<module>/...` file; it must surface as the microservice
    name regardless of which inner module the file belongs to.
    `chat-assign` is single-module so its module and microservice names
    coincide.
    """
    counts = kuzu_graph.microservice_counts()
    assert counts.get("chat-assign", 0) > 0, counts
    assert counts.get("chat-core", 0) > 0, counts
    # Inner module names must NOT appear at the microservice level — that
    # was exactly the misclassification the rename was meant to fix.
    inner = {"chat-app", "chat-engine", "chat-domain", "chat-contracts"}
    assert not (inner & set(counts)), counts


def test_phantom_nodes_for_external_types(kuzu_db_path: Path) -> None:
    """Spring Data repositories extend `JpaRepository` (an external type).

    The builder must materialise that as a *phantom* (unresolved) Symbol so
    EXTENDS/IMPLEMENTS edges are never dangling.
    """
    conn = _connect(kuzu_db_path)
    n_phantoms = _scalar(
        conn, "MATCH (s:Symbol) WHERE s.resolved = false RETURN count(s)"
    )
    assert n_phantoms > 0, "no phantom nodes — external type resolution may be silently dropping edges"


def test_injects_edges_have_mechanism(kuzu_db_path: Path) -> None:
    """Every INJECTS edge should record *how* the injection happens.

    The bank-chat-system uses constructor injection throughout
    (`ChatManagementService(...)`, `ChatCoreJoinClient(...)`), so we expect
    to see at least one `constructor` mechanism. We don't assert that *all*
    edges are constructor-injected to leave room for future Lombok / setter
    samples.
    """
    conn = _connect(kuzu_db_path)
    mechanisms = set(_column(conn, "MATCH ()-[e:INJECTS]->() RETURN DISTINCT e.mechanism"))
    assert "constructor" in mechanisms, mechanisms


def test_routes_and_exposes_populated(kuzu_db_path: Path) -> None:
    conn = _connect(kuzu_db_path)
    assert _scalar(conn, "MATCH (r:Route) RETURN count(r)") >= 1
    assert _scalar(conn, "MATCH ()-[e:EXPOSES]->() RETURN count(e)") >= 1


def test_route_id_includes_microservice(kuzu_db_path_route_extraction_smoke: Path) -> None:
    """Same HTTP path in two declared microservices → distinct Route primary keys."""
    db_path = kuzu_db_path_route_extraction_smoke
    conn = _connect(db_path)
    ids = _column(
        conn,
        "MATCH (r:Route) WHERE r.path = '/api/users' AND r.kind = 'http_endpoint' "
        "RETURN r.id",
    )
    assert len(set(ids)) >= 2, ids


def test_exposes_edge_direction(kuzu_db_path_route_extraction_smoke: Path) -> None:
    db_path = kuzu_db_path_route_extraction_smoke
    conn = _connect(db_path)
    fwd = _scalar(conn, "MATCH (s:Symbol)-[:EXPOSES]->(r:Route) RETURN count(*)")
    rev = _scalar(conn, "MATCH (r:Route)-[:EXPOSES]->(s:Symbol) RETURN count(*)")
    assert fwd >= 1
    assert rev == 0


def test_symbol_has_capabilities_column(kuzu_db_path: Path) -> None:
    """Symbol nodes must have a `capabilities` STRING[] column (ontology v4)."""
    conn = _connect(kuzu_db_path)
    # Simply SELECT a capabilities value — if the column doesn't exist Kuzu raises.
    try:
        r = conn.execute(
            "MATCH (s:Symbol) WHERE s.kind = 'class' AND s.resolved "
            "RETURN s.capabilities LIMIT 1"
        )
    except Exception as exc:
        pytest.fail(f"capabilities column missing or unreadable: {exc}")
    # The column should exist; the value may be an empty list for most types.
    assert r is not None


def test_cli_entrypoint_runs(tmp_path: Path, corpus_root: Path) -> None:
    """`build_ast_graph.py --source-root <root>` must succeed end-to-end.

    This is an integration smoke test — it calls the script as a user would
    (via the venv Python) and asserts a non-empty Kuzu DB is written.
    """
    target = tmp_path / "graph.kuzu"
    script = Path(__file__).resolve().parent.parent / "build_ast_graph.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-root", str(corpus_root),
            "--kuzu-path", str(target),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert target.exists()
    conn = _connect(target)
    assert _scalar(conn, "MATCH (s:Symbol) RETURN count(s)") > 0
