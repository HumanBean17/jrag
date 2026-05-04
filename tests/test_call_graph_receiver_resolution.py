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


def test_resolved_receiver_unindexed_callee_preserves_strategy(tmp_path: Path) -> None:
    """B3: when the receiver type is resolvable via explicit import but is an unindexed external
    type (JDK), the edge must preserve the receiver-tier strategy/confidence rather than
    collapsing to phantom/0.0.

    Uses `import java.util.Objects; Objects.requireNonNull("x")` — a regular (not static)
    import with a static method call — so the receiver FQN is known from the explicit import
    (strategy='import_map', 0.95) but the type is not indexed.  Complements the smoke-fixture
    test (which uses `import static …`) by isolating the failure mode without depending on the
    static-import path.
    """
    root = tmp_path / "proj"
    java = root / "src/main/java/b3test"
    java.mkdir(parents=True)
    (java / "Bad.java").write_text(
        "package b3test;\n"
        "import java.util.Objects;\n"
        "public class Bad {\n"
        "  public void m() {\n"
        "    Objects.requireNonNull(\"x\");\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)

    db_path = tmp_path / "b3.kuzu"
    write_kuzu(db_path, tables, source_root=root, verbose=False)

    conn = _connect(db_path)
    r = conn.execute(
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'b3test.Bad#m' "
        "AND dst.name = 'requireNonNull' "
        "RETURN c.strategy AS s, c.confidence AS conf, c.resolved AS res LIMIT 10"
    )
    rows = []
    while r.has_next():
        rows.append(r.get_next())
    assert rows, "expected a requireNonNull call edge"
    # Receiver FQN is known via explicit import — strategy must NOT be 'phantom'
    strategies = {str(row[0]) for row in rows}
    _VALID = {"import_map", "static_import_wildcard", "unique_type_name", "suffix"}
    assert not (strategies - _VALID), (
        f"B3 bug: edge strategy should be in {_VALID} when receiver known via explicit import; "
        f"got {rows}"
    )
    # Confidence must be preserved from the receiver tier (≥0.55 for suffix, 0.95 for import_map)
    confs = [float(row[1]) for row in rows]
    assert all(c > 0.5 for c in confs), (
        f"B3 bug: confidence should be preserved from receiver tier; got {rows}"
    )
    # resolved=False because the callee node is a phantom (java.util.Objects not indexed)
    assert all(row[2] is False for row in rows), (
        f"expected resolved=False for unindexed callee; got {rows}"
    )


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
