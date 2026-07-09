"""Lexical (graph-only) search backend — keyword search over the LadybugDB symbol graph.

Covers the macOS-Intel fallback: when the vector stack is absent, ``search``
dispatches to ``search_lexical.run_lexical_search``. These tests build a real
fixture graph (no vectors needed) and exercise ranking, NodeFilter pushdown,
dedup, disk snippets, explain rendering, and the absent-graph / sql-yaml edges.
The ``_lexical_where`` parity test guards drift against ``mcp_v2._symbol_where_from_filter``.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from java_codebase_rag.graph.ladybug_queries import LadybugGraph
from java_codebase_rag.mcp.mcp_v2 import NodeFilter, _node_matches_filter, _symbol_where_from_filter
from java_codebase_rag.search.search_lexical import _lexical_where, _read_snippet, run_lexical_search
from java_codebase_rag.search.search_scoring import explain_score_components


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
        {"generated_only": True},
        {"exclude_generated": True},
        {"role": "SERVICE", "exclude_generated": True},
    ]
    for c in cases:
        nf = NodeFilter.model_validate(c)
        assert _lexical_where(nf, path_contains=None) == _symbol_where_from_filter(nf), c
    # path_contains is the lexical-only extension (pushdown into Cypher).
    where, params = _lexical_where(None, path_contains="src/main")
    assert where == "WHERE s.filename CONTAINS $path_contains"
    assert params == {"path_contains": "src/main"}


def test_fetch_limit_is_pool_cap_not_page_derived(tmp_path: Path) -> None:
    """C2 regression: the lexical fetch LIMIT must be _CANDIDATE_LIMIT_CAP (rank the full
    candidate pool in Python), NOT a page-derived value. The MATCH scan has no DB-side
    relevance ORDER BY, so a pagination-derived LIMIT (4x the page — valid on the vector
    path where LanceDB returns rows pre-ranked by similarity) returns only the first ~4xL
    symbols in arbitrary storage order and misses the best match on a real repo.

    A purely behavioral test can't catch this deterministically: the buggy formula
    `(limit+offset)*4` always overfetches enough to fill the windowed page, so only the
    *identity* of returned symbols differs (nondeterministic storage order). Instead we
    capture the LIMIT param sent to the graph and assert it equals the cap, independent of
    the requested page — the exact invariant the fix established. (The earlier count-based
    test used limit=50, under which the buggy formula yields 200 >= the 30-symbol pool, so
    it passed green under the bug — a false sentinel, removed.)"""
    db = _build_corpus(tmp_path)
    g = _graph(db)

    captured: list = []
    real_rows = g._rows

    def _spy(cypher, params):  # capture the LIMIT param the backend sends to the graph
        if "lim" in params:
            captured.append(params["lim"])
        return real_rows(cypher, params)

    g._rows = _spy  # type: ignore[method-assign]

    from java_codebase_rag.search.search_lexical import _CANDIDATE_LIMIT_CAP

    # Default page and a larger page must BOTH request the same cap-sized fetch — the
    # buggy formula would have produced 20 (limit=5) and 80 (limit=20).
    run_lexical_search("distribution", limit=5, graph=g)
    run_lexical_search("distribution", limit=20, graph=g)
    assert captured, "no LIMIT captured — _rows spy did not fire"
    assert all(lim == _CANDIDATE_LIMIT_CAP for lim in captured), (
        f"fetch LIMIT must be the pool cap ({_CANDIDATE_LIMIT_CAP}) independent of page; "
        f"got {captured}"
    )


def test_cap_truncation_advisory_fires_at_cap(monkeypatch, tmp_path: Path) -> None:
    """Correctness review: when the fetch hits the safety cap, deeper matches are never
    ranked (the scan has no ORDER BY). Surface an advisory instead of silently returning a
    storage-order-dependent subset. Lower the cap so a small fixture triggers it."""
    from java_codebase_rag.search import search_lexical

    db = _build_corpus(tmp_path)
    g = _graph(db)
    monkeypatch.setattr(search_lexical, "_CANDIDATE_LIMIT_CAP", 3)
    adv: list[str] = []
    run_lexical_search("distribution", limit=5, graph=g, advisories=adv)
    assert any("repo cap" in a for a in adv), f"cap advisory should fire; got {adv}"


def test_cap_advisory_silent_below_cap(tmp_path: Path) -> None:
    """The cap advisory must NOT fire when the pool fits under the cap (no truncation)."""
    db = _build_corpus(tmp_path)
    g = _graph(db)
    adv: list[str] = []
    run_lexical_search("distribution", limit=5, graph=g, advisories=adv)
    assert adv == [], f"no advisory expected for a small pool; got {adv}"


def test_fqn_contains_member_match_not_dropped(tmp_path: Path) -> None:
    """I1 regression: the shared post-filter _node_matches_filter re-checks fqn_contains
    against row['fqn'] (falling back to primary_type_fqn). For a member node the fqn is
    'Type#method(...)'; the lexical row MUST carry the raw 'fqn' or the post-filter drops
    member-level matches the Cypher pushdown already accepted. This drives the bug
    end-to-end: run the search, then re-check each returned row through the SAME
    _node_matches_filter the real search_v2 loop uses."""
    db = _build_corpus(tmp_path)
    nf = NodeFilter.model_validate({"fqn_contains": "processClientMessage"})
    rows = run_lexical_search("process client message", limit=10, filter=nf, graph=_graph(db))
    assert rows, "Cypher pushdown should have surfaced the member node"
    # The real bug site: a lexical row without the raw member 'fqn' passes the Cypher
    # pushdown but is dropped here (falls back to the bare type fqn).
    assert any(_node_matches_filter("symbol", r, nf) for r in rows), (
        "post-filter dropped the member match: lexical row lacks raw 'fqn' "
        "(only primary_type_fqn); fqns=" + str([r.get("fqn") for r in rows])
    )
    assert any("processClientMessage" in str(r.get("fqn") or "") for r in rows)


def test_explain_import_survives_graph_only_env() -> None:
    """C1 regression: `jrag search --explain` imports explain_score_components at call
    time. On graph-only (macOS Intel) installs `search_lancedb` is unimportable
    (lancedb/sentence-transformers excluded by PEP 508 markers, imported at its module
    top), so the import MUST come from the dependency-free `search_scoring`. Assert both
    that the CLI source imports from search_scoring AND the invariant: blocking the vector
    stack makes search_lancedb unimportable while search_scoring (and the lexical explain
    renderer) stays importable."""
    jrag_py = Path(__file__).resolve().parent.parent / "src" / "java_codebase_rag" / "jrag.py"
    m = re.search(r"from java_codebase_rag\.search\.(search_\w+) import explain_score_components", jrag_py.read_text(encoding="utf-8"))
    assert m, "explain_score_components import not found in jrag.py"
    assert m.group(1) == "search_scoring", (
        f"jrag.py imports explain_score_components from {m.group(1)!r}; it MUST be "
        "'search_scoring' — search_lancedb is unimportable on graph-only (Intel) installs"
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "for m in ('lancedb','pylance','torch','sentence_transformers','cocoindex'):\n"
            "    sys.modules[m] = None\n"
            "try:\n"
            "    from java_codebase_rag.search import search_lancedb\n"
            "    print('LANCEDB:imported')\n"
            "except ModuleNotFoundError:\n"
            "    print('LANCEDB:blocked')\n"
            "from java_codebase_rag.search.search_scoring import explain_score_components\n"
            "print('SCORING:ok')\n"
            "r = explain_score_components("
            "{'name_match':1.0,'type_match':0.1,'fqn_match':0.2,"
            "'lexical_relevance':0.8,'role_weight':0.1}, lexical=True)\n"
            "print('RENDER:' + r)\n",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "LANCEDB:blocked" in proc.stdout, (
        f"search_lancedb imported under graph-only env: {proc.stdout}"
    )
    assert "SCORING:ok" in proc.stdout
    assert "relevance=" in proc.stdout


def test_search_v2_lexical_dispatch_end_to_end(monkeypatch, tmp_path: Path) -> None:
    """Integration (test-review gaps I-1/I-2): drive the REAL search_v2 entry point down
    the lexical branch (run_search=None) against a fixture graph. Verifies the dispatch
    converges on the shared row->hit loop, sets lexical_mode, emits the graph-only mode
    advisory, and that SearchHit carries the expected fqn — none of which the direct
    run_lexical_search unit tests reach. Also covers --explain score_components end-to-end."""
    from java_codebase_rag.mcp import mcp_v2

    db = _build_corpus(tmp_path)
    g = _graph(db)
    monkeypatch.setattr(mcp_v2, "run_search", None)

    out = mcp_v2.search_v2(query="distribution chunk", graph=g)
    assert out.success, f"expected success; message={out.message}"
    assert out.lexical_mode is True
    assert out.results, "expected >=1 hit through the shared row->hit loop"
    assert out.results[0].fqn == "svc.DistributionChunkService"
    assert any("graph-only" in a for a in (out.advisories or [])), out.advisories

    out2 = mcp_v2.search_v2(query="distribution chunk", graph=g, explain=True)
    assert out2.success and out2.results
    assert out2.results[0].score_components, "explain should populate score_components"
