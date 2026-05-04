"""Isolated call-graph resolution checks (minimal Java trees under tmp_path).

The session `kuzu_graph` fixture uses bank-chat-system only; these tests build
tiny graphs so we can assert on a single known failure mode without coupling
to the large corpus.
"""
from __future__ import annotations

from pathlib import Path

import kuzu

from build_ast_graph import GraphTables, pass1_parse, pass2_edges, pass3_calls, write_kuzu


def _connect(db_path: Path) -> kuzu.Connection:
    return kuzu.Connection(kuzu.Database(str(db_path), read_only=True))


def test_receiver_disambiguation_uses_type_index_not_method_unique(tmp_path: Path) -> None:
    """An unresolved receiver id must not pick a type via globally-unique *method* name.

    If `helper` is not in scope but exactly one method `helper()` exists in the
    project, the receiver type must not become that method's declaring class.
    """
    root = tmp_path / "proj"
    java = root / "src/main/java/cgrisol"
    java.mkdir(parents=True)
    (java / "Service.java").write_text(
        "package cgrisol;\n"
        "public class Service {\n"
        "  public void helper() {}\n"
        "  public void run() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (java / "Bad.java").write_text(
        "package cgrisol;\n"
        "public class Bad {\n"
        "  public void m() {\n"
        "    helper.run();\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)

    db_path = tmp_path / "cg.kuzu"
    write_kuzu(db_path, tables, source_root=root, verbose=False)

    conn = _connect(db_path)
    r = conn.execute(
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'cgrisol.Bad#' AND src.name = 'm' "
        "AND dst.fqn STARTS WITH 'cgrisol.Service#' AND dst.name = 'run' "
        "AND c.resolved = true "
        "RETURN count(*) AS n"
    )
    assert r.has_next()
    n = int(r.get_next()[0] or 0)
    assert n == 0, (
        "expected no resolved CALLS edge Bad.m -> Service.run when `helper` "
        "is not a type and is not in scope"
    )
