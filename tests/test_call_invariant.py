from __future__ import annotations

from pathlib import Path

import kuzu

from build_ast_graph import GraphTables, pass1_parse, pass2_edges, pass3_calls, write_kuzu
from kuzu_queries import KuzuGraph


_FQN_COLLISION_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fqn_collision_smoke"
_CROSS_SERVICE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cross_service_smoke"


def _build(root: Path, db_path: Path) -> None:
    """Build through pass3 only (no routes); sufficient for `pass3_skipped_cross_service` assertions."""
    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    write_kuzu(db_path, tables, source_root=root, verbose=False)


def _scalar(db_path: Path, query: str) -> int:
    conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))
    r = conn.execute(query)
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def test_call_invariant_blocks_cross_microservice_edges(tmp_path: Path) -> None:
    db = tmp_path / "fqn_collision.kuzu"
    _build(_FQN_COLLISION_FIXTURE, db)
    cross_calls = _scalar(
        db,
        "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) "
        "WHERE a.microservice <> '' AND b.microservice <> '' "
        "AND a.microservice <> b.microservice "
        "RETURN count(*)",
    )
    assert cross_calls == 0
    assert KuzuGraph(str(db)).meta()["pass3_skipped_cross_service"] >= 1


def test_call_invariant_inert_on_clean_fixtures(tmp_path: Path) -> None:
    db = tmp_path / "cross_service_smoke.kuzu"
    _build(_CROSS_SERVICE_FIXTURE, db)
    assert KuzuGraph(str(db)).meta()["pass3_skipped_cross_service"] == 0


def test_call_invariant_inert_on_bank_chat_system(tmp_path: Path, corpus_root: Path) -> None:
    db = tmp_path / "bank_chat.kuzu"
    _build(corpus_root, db)
    assert KuzuGraph(str(db)).meta()["pass3_skipped_cross_service"] == 0
