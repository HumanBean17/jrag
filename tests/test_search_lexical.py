"""Lexical (graph-only) search backend — keyword search over the LadybugDB symbol graph.

Covers the macOS-Intel fallback: when the vector stack is absent, ``search``
dispatches to ``search_lexical.run_lexical_search``. These tests build a real
fixture graph (no vectors needed) and exercise ranking, NodeFilter pushdown,
dedup, disk snippets, explain rendering, and the absent-graph / sql-yaml edges.
The ``_lexical_where`` parity test guards drift against ``mcp_v2._symbol_where_from_filter``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ladybug_queries import LadybugGraph
from mcp_v2 import NodeFilter, _symbol_where_from_filter
from search_lexical import _lexical_where, _read_snippet, run_lexical_search
from search_scoring import explain_score_components


def _build_corpus(tmp_path: Path) -> Path:
    """Write a tiny Java corpus and build a LadybugDB graph; return the db path."""
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "DistributionChunkService.java").write_text(
        "package svc;\n"
        "@Service\n"
        "public class DistributionChunkService {\n"
        "    public void processClientMessage(String message) {\n"
        "        // distribute the client message to operators\n"
        "    }\n"
        "    public void distributeChunk(Object chunk) {\n"
        "        // handle chunk distribution\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "ctrl").mkdir()
    (tmp_path / "ctrl" / "OperatorSessionController.java").write_text(
        "package ctrl;\n"
        "@RestController\n"
        "public class OperatorSessionController {\n"
        "    public void handleOperatorRequest() {\n"
        "        // handle the operator session request\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "CustomerDto.java").write_text(
        "package model;\n"
        "public class CustomerDto {\n"
        "    private String name;\n"
        "}\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "g.lbug"
    from _builders import build_ladybug_to

    build_ladybug_to(tmp_path, db_path, max_pass=3)
    return db_path


def _graph(db_path: Path) -> LadybugGraph:
    # Fresh read-only instance — bypasses the LadybugGraph singleton so tests are
    # isolated from each other's tmp_path.
    return LadybugGraph(str(db_path))


def test_keyword_name_match_ranks_expected_symbol_first(tmp_path: Path) -> None:
    db = _build_corpus(tmp_path)
    rows = run_lexical_search("distribution chunk service", limit=5, graph=_graph(db))
    assert rows, "expected at least one lexical hit"
    top = rows[0]
    # Dedup (default on) collapses the type + its methods to the enclosing type fqn.
    assert top["primary_type_fqn"] == "svc.DistributionChunkService"
    assert top["_kind"] == "java"
    assert top["kind"] == "class"
    assert 0.0 <= top["_score"] <= 1.0
    # Row carries the keys _row_to_search_hit / _node_matches_filter consume.
    for k in ("filename", "text", "start", "end", "symbol_id", "annotations", "capabilities"):
        assert k in top
    assert isinstance(top["start"], dict) and "byte_offset" in top["start"]


def test_role_filter_pushdown_returns_only_service(tmp_path: Path) -> None:
    db = _build_corpus(tmp_path)
    nf = NodeFilter.model_validate({"role": "SERVICE"})
    rows = run_lexical_search("distribution", limit=10, filter=nf, graph=_graph(db))
    assert rows, "expected the @Service type under role=SERVICE"
    for r in rows:
        assert r.get("role") == "SERVICE"
    # The @RestController type must NOT leak in under role=SERVICE.
    assert all("OperatorSession" not in (r.get("primary_type_fqn") or "") for r in rows)


def test_role_filter_picks_controller(tmp_path: Path) -> None:
    db = _build_corpus(tmp_path)
    nf = NodeFilter.model_validate({"role": "CONTROLLER"})
    rows = run_lexical_search("operator session", limit=10, filter=nf, graph=_graph(db))
    assert rows, "expected the @RestController type under role=CONTROLLER"
    for r in rows:
        assert r.get("role") == "CONTROLLER"


def test_dedup_collapses_members_of_same_type(tmp_path: Path) -> None:
    db = _build_corpus(tmp_path)
    g = _graph(db)
    collapsed = run_lexical_search("distribution", limit=10, dedup=True, graph=g)
    expanded = run_lexical_search("distribution", limit=10, dedup=False, graph=g)
    # The type + its methods all share primary_type_fqn 'svc.DistributionChunkService'.
    assert len(expanded) >= 2, "expected the type plus at least one method to be indexed"
    assert len(collapsed) < len(expanded)
    assert all(r.get("primary_type_fqn") == "svc.DistributionChunkService" for r in collapsed)
    assert any(r.get("_chunks_collapsed", 1) >= 2 for r in collapsed), \
        "dedup should collapse type+methods into one survivor annotated _chunks_collapsed"


def test_path_contains_pushdown(tmp_path: Path) -> None:
    db = _build_corpus(tmp_path)
    rows = run_lexical_search(
        "distribution chunk", limit=10, path_contains="svc", graph=_graph(db)
    )
    assert rows
    assert all("svc" in (r.get("filename") or "") for r in rows)


def test_snippet_read_from_disk_and_fallback(tmp_path: Path) -> None:
    f = tmp_path / "X.java"
    f.write_text("line0\npublic class Foo {\n  void bar() {}\n}\n", encoding="utf-8")
    # Real file, lines 2..4 → declaration lines.
    assert "class Foo" in _read_snippet(str(tmp_path), "X.java", 2, 4, "void bar()", "Foo")
    # Missing file → signature fallback.
    assert _read_snippet(str(tmp_path), "nope.java", 1, 1, "void fallback()", "Foo") == "void fallback()"
    # No source root + relative path → signature fallback (don't read from cwd).
    assert _read_snippet("", "rel.java", 1, 1, "sig()", "F") == "sig()"


def test_explain_components_populated(tmp_path: Path) -> None:
    db = _build_corpus(tmp_path)
    rows = run_lexical_search("distribution chunk", limit=3, explain=True, graph=_graph(db))
    assert rows
    comps = rows[0]["_score_components"]
    for k in ("name_match", "type_match", "fqn_match", "lexical_relevance", "role_weight"):
        assert k in comps
    rendered = explain_score_components(comps, lexical=True)
    assert "relevance=" in rendered
    # Non-lexical rendering must NOT surface lexical keys (regression guard).
    assert "relevance=" not in explain_score_components(comps, lexical=False)


def test_table_sql_and_yaml_return_empty(tmp_path: Path) -> None:
    g = _graph(_build_corpus(tmp_path))
    assert run_lexical_search("anything", table="sql", graph=g) == []
    assert run_lexical_search("anything", table="yaml", graph=g) == []


def test_no_graph_raises_clean_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(tmp_path / "no_such_idx"))
    with pytest.raises(RuntimeError, match="lexical search unavailable"):
        run_lexical_search("distribution", graph=None)


def test_lexical_where_parity_with_mcp_v2() -> None:
    cases = [
        {},
        {"role": "SERVICE"},
        {"module": "m", "capability": "C"},
        {"fqn_contains": "Foo", "annotation": "Bar"},
        {"microservice": "ms", "exclude_roles": ["DTO"]},
        {"symbol_kind": "class"},
        {"symbol_kinds": ["class", "interface"]},
    ]
    for c in cases:
        nf = NodeFilter.model_validate(c)
        assert _lexical_where(nf, path_contains=None) == _symbol_where_from_filter(nf), c
    # path_contains is the lexical-only extension (pushdown into Cypher).
    where, params = _lexical_where(None, path_contains="src/main")
    assert where == "WHERE s.filename CONTAINS $path_contains"
    assert params == {"path_contains": "src/main"}
