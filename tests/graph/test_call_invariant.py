from __future__ import annotations

from pathlib import Path

import ladybug

from java_codebase_rag.graph.ladybug_queries import LadybugGraph


def _scalar(db_path: Path, query: str) -> int:
    conn = ladybug.Connection(ladybug.Database(str(db_path), read_only=True))
    r = conn.execute(query)
    return int(r.get_next()[0] or 0) if r.has_next() else 0


def test_call_invariant_blocks_cross_microservice_edges(ladybug_db_path_fqn_collision_smoke: Path) -> None:
    db = ladybug_db_path_fqn_collision_smoke
    cross_calls = _scalar(
        db,
        "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) "
        "WHERE a.microservice <> '' AND b.microservice <> '' "
        "AND a.microservice <> b.microservice "
        "RETURN count(*)",
    )
    assert cross_calls == 0
    assert LadybugGraph(str(db)).meta()["pass3_skipped_cross_service"] >= 1


def test_call_invariant_inert_on_clean_fixtures(ladybug_db_path_cross_service_smoke: Path) -> None:
    assert LadybugGraph(str(ladybug_db_path_cross_service_smoke)).meta()["pass3_skipped_cross_service"] == 0


def test_call_invariant_inert_on_bank_chat_system(ladybug_graph: LadybugGraph) -> None:
    assert ladybug_graph.meta()["pass3_skipped_cross_service"] == 0
