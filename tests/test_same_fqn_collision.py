"""Task 16: same-FQN collision warning in ``_register_type``.

When two distinct source files register a type with the SAME fully-qualified
name (the Kotlin+Java mixed-repo collision case — e.g. ``com.example.Foo`` in
both ``Foo.java`` and ``Foo.kt``), the graph builder silently kept whichever
was registered last (last-wins). Task 16 keeps last-wins semantics but emits a
warning naming the FQN and both file paths so the collision is visible.

This is a pure unit test of ``_register_type`` — no parsing, no corpus, and
intentionally NOT Kotlin-gated (the warning fires for any same-FQN collision
regardless of language).
"""
from __future__ import annotations

import logging

from java_codebase_rag.ast.ast_java import TypeDecl
from java_codebase_rag.graph.build_ast_graph import GraphTables, _register_type


def _make_decl(fqn: str) -> TypeDecl:
    name = fqn.rsplit(".", 1)[-1]
    return TypeDecl(name=name, kind="class", fqn=fqn)


def test_same_fqn_different_file_warns_and_last_wins(caplog) -> None:
    """Two ``_register_type`` calls with identical ``fqn`` but different
    ``file_path``: a WARNING is emitted (naming the fqn + both paths), the
    build does not crash, and registration is still last-wins."""
    tables = GraphTables()
    fqn = "com.example.Foo"
    path_java = "src/main/java/com/example/Foo.java"
    path_kt = "src/main/java/com/example/Foo.kt"

    _register_type(
        tables, _make_decl(fqn),
        file_path=path_java,
        module="mod", microservice="svc", outer_fqn=None,
    )

    caplog.set_level(logging.WARNING, logger="java_codebase_rag.graph.build_ast_graph")
    _register_type(
        tables, _make_decl(fqn),
        file_path=path_kt,
        module="mod", microservice="svc", outer_fqn=None,
    )

    # A warning fired naming the FQN and both file paths.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a same-FQN collision warning; got none"
    msg = warnings[-1].getMessage()
    assert fqn in msg, msg
    assert path_java in msg, msg
    assert path_kt in msg, msg

    # Last-wins registration is preserved (no crash, entry overwritten).
    assert fqn in tables.types, tables.types.keys()
    assert tables.types[fqn].file_path == path_kt, tables.types[fqn].file_path


def test_same_fqn_same_file_no_warning(caplog) -> None:
    """Re-registering the same FQN from the SAME file_path (e.g. an incremental
    re-parse of one file) must NOT warn — only a cross-file collision warns."""
    tables = GraphTables()
    fqn = "com.example.Bar"
    path = "src/main/java/com/example/Bar.java"

    _register_type(
        tables, _make_decl(fqn),
        file_path=path,
        module="mod", microservice="svc", outer_fqn=None,
    )
    caplog.set_level(logging.WARNING, logger="java_codebase_rag.graph.build_ast_graph")
    _register_type(
        tables, _make_decl(fqn),
        file_path=path,
        module="mod", microservice="svc", outer_fqn=None,
    )

    collisions = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and fqn in r.getMessage()
    ]
    assert not collisions, [r.getMessage() for r in collisions]
