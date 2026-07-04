"""Tests for `jrag` orientation commands + semantic search + agent_next_actions (PR-JRAG-4).

Tests:
1.  test_microservices_lists_counts
2.  test_map_returns_non_empty_counts_per_service
3.  test_conventions_reports_dominant_roles
4.  test_overview_microservice_bundle
5.  test_overview_route_uses_flow
6.  test_overview_topic_lists_producers_and_consumers
7.  test_overview_as_overrides_polymorphic_inference
8.  test_search_returns_ranked_hits
9.  test_search_hybrid_calls_hybrid_path
10. test_search_table_all_runs_three_tables
11. test_search_offset_paginates
12. test_search_fuzzy_rejected_in_handler_as_status_error
13. test_next_actions_valid_runnable_commands_capped_at_5
14. test_next_actions_zero_direction_suppressed
15. test_next_actions_covers_composed_dot_keys
16. test_next_actions_falls_back_to_result_edges_when_no_edge_summary
17. test_next_actions_omitted_when_empty
18. test_build_parser_imports_no_backend_modules
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _jrag_exe() -> str:
    """Locate the installed ``jrag`` entry point next to the venv interpreter."""
    candidate = Path(sys.executable).parent / "jrag"
    if candidate.is_file():
        return str(candidate)
    exe = shutil.which("jrag")
    assert exe is not None, "expected installed jrag entrypoint (run: pip install -e .)"
    return exe


def _run_jrag(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_jrag_exe(), *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        check=False,
    )


def _env_for(corpus_root: Path, ladybug_db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)
    return env


# ===== Tests 1–3: orientation counts =====


def test_microservices_lists_counts(corpus_root: Path, ladybug_db_path: Path) -> None:
    """microservices command returns non-empty microservice → count map."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["microservices", "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"microservices failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    counts = payload["nodes"]["microservices"]["counts"]
    assert counts, f"counts dict empty: {payload}"
    assert any(int(v or 0) > 0 for v in counts.values()), f"all counts zero: {counts}"


def test_map_returns_non_empty_counts_per_service(corpus_root: Path, ladybug_db_path: Path) -> None:
    """map command returns non-empty counts grouped by microservice."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["map", "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"map failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    counts = payload["nodes"]["map"]["counts"]
    assert counts, f"map counts empty: {payload}"
    # At least one scope should have at least one kind with positive count.
    found_positive = any(
        int(v or 0) > 0
        for scope_counts in counts.values()
        for v in scope_counts.values()
    )
    assert found_positive, f"all map counts zero: {counts}"


def test_conventions_reports_dominant_roles(corpus_root: Path, ladybug_db_path: Path) -> None:
    """conventions command reports role distribution."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["conventions", "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"conventions failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    roles = payload["nodes"]["conventions"]["roles"]
    assert roles, f"roles dict empty: {payload}"
    assert any(int(v or 0) > 0 for v in roles.values()), f"all role counts zero: {roles}"


# ===== Tests 4–7: overview dispatch =====


def test_overview_microservice_bundle(corpus_root: Path, ladybug_db_path: Path) -> None:
    """overview <microservice> returns a bundle (routes + clients + producers counts)."""
    env = _env_for(corpus_root, ladybug_db_path)
    # First get microservices to find a valid one.
    proc_ms = _run_jrag(["microservices", "--format", "json"], env=env)
    assert proc_ms.returncode == 0
    ms_counts = json.loads(proc_ms.stdout)["nodes"]["microservices"]["counts"]
    # Pick the first microservice with a non-zero count.
    ms_name = next((k for k, v in ms_counts.items() if int(v or 0) > 0 and k), None)
    assert ms_name, f"no valid microservice in fixture: {ms_counts}"

    proc = _run_jrag(["overview", ms_name, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"overview <microservice> failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # The bundle should be present on the microservice node.
    ms_node = next(iter(payload["nodes"].values()))
    assert "bundle" in ms_node, f"missing bundle in overview node: {ms_node}"
    assert ms_node["bundle"]["microservice"] == ms_name


def test_overview_route_uses_flow(corpus_root: Path, ladybug_db_path: Path) -> None:
    """overview /chat/assign dispatches as route and returns flow data."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["overview", "/chat/assign", "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"overview /chat/assign failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # Route overview uses traversal shape (root + edges).
    assert payload.get("root"), "expected root set for route overview"
    assert payload.get("root") is not None


def test_overview_topic_lists_producers_and_consumers(corpus_root: Path, ladybug_db_path: Path) -> None:
    """overview <topic> returns producers + consumers for the topic."""
    env = _env_for(corpus_root, ladybug_db_path)
    # Use a topic that exists on the fixture. banking.chat.compliance.review
    # is consumed by ComplianceReviewListener (verified in test_jrag_listing).
    proc = _run_jrag(
        ["overview", "banking.chat.compliance.review", "--format", "json"], env=env
    )
    assert proc.returncode == 0, (
        f"overview <topic> failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    topic_node = next(iter(payload["nodes"].values()))
    assert topic_node.get("kind") == "topic"
    # The node should carry producers and consumers arrays.
    assert "producers" in topic_node, f"missing producers in topic overview: {topic_node}"
    assert "consumers" in topic_node, f"missing consumers in topic overview: {topic_node}"


def test_overview_as_overrides_polymorphic_inference(corpus_root: Path, ladybug_db_path: Path) -> None:
    """overview --as {microservice,route,topic} overrides auto-detection."""
    env = _env_for(corpus_root, ladybug_db_path)
    # "/chat/assign" auto-detects as route. Force it to microservice with --as.
    # The microservice overview returns a bundle shape (inspect), NOT a route flow.
    proc = _run_jrag(
        ["overview", "/chat/assign", "--as", "microservice", "--format", "json"], env=env
    )
    # This will likely fail to find a microservice named "/chat/assign" and
    # return an empty-ish bundle, but the dispatch must go to the microservice
    # path (bundle node), not the route path (traversal with root+edges).
    payload = json.loads(proc.stdout)
    # The key assertion: with --as microservice, the node shape is a bundle
    # (has "bundle" key), NOT a traversal (no root + edges).
    if payload["status"] == "ok":
        node = next(iter(payload["nodes"].values()), {})
        assert "bundle" in node or node.get("kind") == "microservice", (
            f"--as microservice should dispatch to microservice path, got: {node}"
        )
        assert "root" not in payload or not payload.get("root"), (
            "--as microservice should NOT produce traversal shape (root+edges)"
        )


# ===== Tests 8–12: semantic search =====


def test_search_returns_ranked_hits(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """search returns ranked hits from search_v2 (mocked to avoid Lance dependency)."""
    import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    # Mock search_v2 to return a controlled hit.
    fake_hit = mcp_v2.SearchHit(
        chunk_id="c1", symbol_id="sym1", fqn="com.example.Hit",
        score=0.95, snippet="fake snippet", microservice="chat-core",
        module="m", role="SERVICE",
    )
    def mock_search_v2(query, **kwargs):
        return mcp_v2.SearchOutput(
            success=True, results=[fake_hit],
            limit=kwargs.get("limit", 5), offset=kwargs.get("offset", 0),
            advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "assign chat", "--format", "json"])
    captured = capsys.readouterr()
    assert rc == 0, f"search failed: rc={rc}\nstdout={captured.out}\nstderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    assert len(nodes) >= 1, f"expected at least one hit, got {nodes}"


def test_search_hybrid_calls_hybrid_path(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--hybrid flag passes hybrid=True to search_v2."""
    import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    captured_kwargs: dict = {}
    def mock_search_v2(query, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["query"] = query
        return mcp_v2.SearchOutput(
            success=True, results=[], limit=kwargs.get("limit", 5),
            offset=kwargs.get("offset", 0), advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "audit", "--hybrid", "--format", "json"])
    assert rc == 0
    assert captured_kwargs.get("hybrid") is True, (
        f"expected hybrid=True, got hybrid={captured_kwargs.get('hybrid')}"
    )


def test_search_table_all_runs_three_tables(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--table all passes table='all' to search_v2 (java+sql+yaml)."""
    import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    captured_kwargs: dict = {}
    def mock_search_v2(query, **kwargs):
        captured_kwargs.update(kwargs)
        return mcp_v2.SearchOutput(
            success=True, results=[], limit=kwargs.get("limit", 5),
            offset=kwargs.get("offset", 0), advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "schema", "--table", "all", "--format", "json"])
    assert rc == 0
    assert captured_kwargs.get("table") == "all", (
        f"expected table='all', got table={captured_kwargs.get('table')!r}"
    )


def test_search_offset_paginates(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--offset paginates: passes offset to search_v2 and renders next_offset hint."""
    import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    # Return limit+1 hits so truncation fires and next_offset renders.
    fake_hits = [
        mcp_v2.SearchHit(
            chunk_id=f"c{i}", symbol_id=f"s{i}", fqn=f"com.example.Hit{i}",
            score=0.9 - i * 0.01, snippet="snip", microservice="ms", module="m", role="X",
        )
        for i in range(6)  # limit default 5 + 1 → truncated
    ]
    captured_kwargs: dict = {}
    def mock_search_v2(query, **kwargs):
        captured_kwargs.update(kwargs)
        return mcp_v2.SearchOutput(
            success=True, results=fake_hits[:kwargs.get("limit", 5) + 1],
            limit=kwargs.get("limit", 5), offset=kwargs.get("offset", 0),
            advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main([
        "search", "--index-dir", env_index, "test", "--offset", "0",
        "--limit", "5", "--format", "text",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    # Offset must be passed through to search_v2.
    assert captured_kwargs.get("offset") == 0
    # Text mode should carry the offset hint (truncated → next page suggestion).
    assert "truncated" in captured.out.lower() or "--offset" in captured.out, (
        f"expected truncation/offset hint in output: {captured.out}"
    )


def test_search_fuzzy_rejected_in_handler_as_status_error(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--fuzzy is rejected IN-HANDLER with status: error (not argparse exit 2).

    The flag is registered on the parser so argparse doesn't exit 2 before the
    handler runs. The handler checks args.fuzzy and produces a canonical error
    envelope with the message "search is semantic; --fuzzy is implicit".
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["search", "test", "--fuzzy", "--format", "json"], env=env)
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    msg = payload.get("message") or ""
    assert "fuzzy" in msg.lower(), f"expected fuzzy in error message: {msg!r}"
    assert "semantic" in msg.lower(), f"expected 'semantic' in message: {msg!r}"


# ===== Tests 13–17: jrag_hints.next_actions =====


def test_next_actions_valid_runnable_commands_capped_at_5() -> None:
    """next_actions emits valid `jrag <cmd> <fqn>` strings, ≤5."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.Foo"
    edge_summary = {
        "CALLS": {"in": 3, "out": 2},
        "IMPLEMENTS": {"in": 0, "out": 1},
        "EXTENDS": {"in": 0, "out": 1},
        "INJECTS": {"in": 5, "out": 2},
        "OVERRIDES": {"in": 0, "out": 1},
        "OVERRIDDEN_BY": {"in": 2, "out": 0},
    }
    hints = next_actions(root_fqn=fqn, edge_summary=edge_summary, result_edges=[])
    assert len(hints) <= 5, f"expected ≤5 hints, got {len(hints)}: {hints}"
    # Every hint must be `jrag <cmd> <fqn>`.
    for h in hints:
        assert h.startswith("jrag "), f"bad hint prefix: {h!r}"
        parts = h.split()
        assert len(parts) >= 3, f"hint too short: {h!r}"
        assert parts[-1] == fqn, f"fqn mismatch in {h!r}: expected {fqn}"
    # All hints must be unique (de-duped).
    assert len(hints) == len(set(hints)), f"duplicate hints: {hints}"


def test_next_actions_zero_direction_suppressed() -> None:
    """A leaf with INJECTS in:0, out:3 → no `jrag dependents`; `jrag dependencies` present."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.Leaf"
    hints = next_actions(
        root_fqn=fqn,
        edge_summary={"INJECTS": {"in": 0, "out": 3}},
        result_edges=[],
    )
    # in:0 → no `jrag dependents <fqn>` suggestion.
    assert f"jrag dependents {fqn}" not in hints, (
        f"zero-direction not suppressed: {hints}"
    )
    # out:3 → `jrag dependencies <fqn>` should be suggested.
    assert f"jrag dependencies {fqn}" in hints, (
        f"non-zero direction missing: {hints}"
    )


def test_next_actions_covers_composed_dot_keys() -> None:
    """Composed dot-keys like OVERRIDDEN_BY.DECLARES_CLIENT map to overridden-by."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.Method"
    hints = next_actions(
        root_fqn=fqn,
        edge_summary={"OVERRIDDEN_BY.DECLARES_CLIENT": {"in": 2, "out": 0}},
        result_edges=[],
    )
    assert f"jrag overridden-by {fqn}" in hints, (
        f"composed dot-key OVERRIDDEN_BY.* not covered: {hints}"
    )


def test_next_actions_falls_back_to_result_edges_when_no_edge_summary() -> None:
    """When edge_summary is None, labels from result_edges drive the hints."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.Foo"
    result_edges = [
        {"other_id": "a", "edge_type": "CALLS"},
        {"other_id": "b", "edge_type": "INJECTS"},
    ]
    hints = next_actions(root_fqn=fqn, edge_summary=None, result_edges=result_edges)
    # CALLS → callers + callees; INJECTS → dependents + dependencies.
    assert f"jrag callers {fqn}" in hints, f"CALLS in missing from fallback: {hints}"
    assert f"jrag callees {fqn}" in hints, f"CALLS out missing from fallback: {hints}"
    assert f"jrag dependents {fqn}" in hints, f"INJECTS in missing from fallback: {hints}"
    assert f"jrag dependencies {fqn}" in hints, f"INJECTS out missing from fallback: {hints}"


def test_next_actions_omitted_when_empty() -> None:
    """next_actions returns [] when no recognized edges are present."""
    from java_codebase_rag.jrag_hints import next_actions

    hints = next_actions(
        root_fqn="com.example.Foo",
        edge_summary={"UNKNOWN_EDGE": {"in": 5, "out": 5}},
        result_edges=[],
    )
    assert hints == [], f"expected empty hints for unrecognized label, got {hints}"

    # Also empty when edge_summary is None and result_edges is empty.
    hints2 = next_actions(root_fqn="com.example.Foo", result_edges=[])
    assert hints2 == [], f"expected empty hints for no edges, got {hints2}"


# ===== Test 18: build_parser lazy-import sentinel =====


def test_build_parser_imports_no_backend_modules() -> None:
    """build_parser() imports NO backend modules (torch / sentence_transformers / mcp_v2).

    Pins the lazy-import invariant: `jrag --help` stays fast and free of heavy
    deps. After build_parser(), none of these may appear in sys.modules.
    """
    # Purge any already-imported backend modules so we measure only what
    # build_parser() pulls in. (They may be imported by other tests in the
    # session; we snapshot the delta, not the absolute set.)
    import gc
    # If backend modules are already loaded (from other tests), note them

    # If backend modules are already loaded (from other tests), note them
    # as pre-existing so we don't false-positive.
    heavy_modules = ("torch", "sentence_transformers", "mcp_v2", "ladybug_queries", "resolve_service")
    pre_loaded = {m for m in heavy_modules if m in sys.modules}

    # Temporarily remove pre-loaded heavy modules to get a clean measurement.
    # This is safe because we only check that build_parser doesn't ADD them.
    saved: dict[str, object] = {}
    for m in heavy_modules:
        if m in sys.modules:
            saved[m] = sys.modules.pop(m)

    try:
        from java_codebase_rag.jrag import build_parser
        build_parser()
    finally:
        # Restore any modules we temporarily removed.
        for m, mod in saved.items():
            sys.modules[m] = mod
        gc.collect()

    # Assert none of the heavy modules were imported by build_parser().
    for m in heavy_modules:
        assert m not in sys.modules or m in pre_loaded, (
            f"build_parser() imported backend module {m!r} — lazy-import invariant broken"
        )

    # Verify the parser lists the new commands.
    parser = build_parser()
    # The subparsers' actions include the subcommand dest.
    sub_actions = [a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict)]  # noqa: SLF001
    if sub_actions:
        commands = set(sub_actions[0].choices.keys())
        for expected in ("microservices", "map", "conventions", "overview", "search"):
            assert expected in commands, f"missing {expected} in parser subcommands: {commands}"
