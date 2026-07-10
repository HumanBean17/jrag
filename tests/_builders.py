"""Thin wrappers around production `build_ast_graph` passes for tests only."""

from __future__ import annotations

from pathlib import Path

from java_codebase_rag.graph.build_ast_graph import (
    GraphTables,
    pass1_parse,
    pass2_edges,
    pass3_calls,
    pass4_routes,
    pass5_imperative_edges,
    pass6_match_edges,
    write_ladybug,
)


def build_graph_tables_to(corpus_root: Path, *, max_pass: int) -> GraphTables:
    """Run pass1 through ``max_pass`` (inclusive) and return ``GraphTables``.

    ``max_pass`` must be 3, 4, 5, or 6 (pass2 always follows pass1).
    """
    if max_pass not in (3, 4, 5, 6):
        raise ValueError(f"max_pass must be 3..6, got {max_pass}")
    tables = GraphTables()
    asts = pass1_parse(corpus_root, tables, verbose=False)
    pass2_edges(tables, asts, verbose=False)
    pass3_calls(tables, asts, verbose=False)
    if max_pass >= 4:
        pass4_routes(tables, asts, source_root=corpus_root, verbose=False)
    if max_pass >= 5:
        pass5_imperative_edges(tables, asts, source_root=corpus_root, verbose=False)
    if max_pass >= 6:
        pass6_match_edges(tables, verbose=False)
    return tables


def build_ladybug_to(corpus_root: Path, db_path: Path, *, max_pass: int) -> Path:
    """Build through ``max_pass``, ``write_ladybug`` to ``db_path``; return ``db_path``."""
    tables = build_graph_tables_to(corpus_root, max_pass=max_pass)
    write_ladybug(db_path, tables, source_root=corpus_root, verbose=False)
    return db_path


def build_ladybug_into(corpus_root: Path, db_path: Path) -> Path:
    """Tier-3 helper: pass1–4 + ``write_ladybug`` (mutable per-test corpus under ``corpus_root``)."""
    return build_ladybug_to(corpus_root, db_path, max_pass=4)


def build_ladybug_imperative_into(corpus_root: Path, db_path: Path) -> Path:
    """pass1–5 + ``write_ladybug`` (no pass6)."""
    return build_ladybug_to(corpus_root, db_path, max_pass=5)


def build_ladybug_full_into(corpus_root: Path, db_path: Path) -> Path:
    """pass1–6 + ``write_ladybug``."""
    return build_ladybug_to(corpus_root, db_path, max_pass=6)
