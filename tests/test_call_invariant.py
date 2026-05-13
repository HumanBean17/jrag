from __future__ import annotations

from pathlib import Path

import kuzu

from kuzu_queries import KuzuGraph


def _scalar(db_path: Path, query: str) -> int:
    conn = kuzu.Connection(kuzu.Database(str(db_path), read_only=True))
    r = conn.execute(query)
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def test_call_invariant_blocks_cross_microservice_edges(kuzu_db_path_fqn_collision_smoke: Path) -> None:
    db = kuzu_db_path_fqn_collision_smoke
    cross_calls = _scalar(
        db,
        "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) "
        "WHERE a.microservice <> '' AND b.microservice <> '' "
        "AND a.microservice <> b.microservice "
        "RETURN count(*)",
    )
    assert cross_calls == 0
    assert KuzuGraph(str(db)).meta()["pass3_skipped_cross_service"] >= 1


def test_call_invariant_inert_on_clean_fixtures(kuzu_db_path_cross_service_smoke: Path) -> None:
    assert KuzuGraph(str(kuzu_db_path_cross_service_smoke)).meta()["pass3_skipped_cross_service"] == 0


def test_call_invariant_inert_on_bank_chat_system(kuzu_graph: KuzuGraph) -> None:
    assert kuzu_graph.meta()["pass3_skipped_cross_service"] == 0
