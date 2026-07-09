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
    """overview --as microservice forces the microservice dispatch path even for
    a subject that auto-detects as a route (/chat/assign).

    The node shape is a bundle (inspect), NOT a traversal (root + edges). The
    prior `if payload["status"] == "ok":` guard made this test vacuously pass on
    any non-ok status — now the dispatch is asserted unconditionally.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["overview", "/chat/assign", "--as", "microservice", "--format", "json"], env=env
    )
    assert proc.returncode == 0, (
        f"overview --as microservice failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got: {payload}"
    node = next(iter(payload["nodes"].values()), {})
    # --as microservice dispatches to the microservice path (bundle node), NOT
    # the route path (traversal with root + edges).
    assert "bundle" in node or node.get("kind") == "microservice", (
        f"--as microservice should dispatch to microservice path, got: {node}"
    )
    assert not payload.get("root"), (
        f"--as microservice must NOT produce traversal shape (root+edges): {payload}"
    )


# ===== Tests 8–12: semantic search =====


def test_search_returns_ranked_hits(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """search returns ranked hits from search_v2 (mocked to avoid Lance dependency)."""
    from java_codebase_rag.mcp import mcp_v2
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
    from java_codebase_rag.mcp import mcp_v2
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
    from java_codebase_rag.mcp import mcp_v2
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
    from java_codebase_rag.mcp import mcp_v2
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


# ===== Phase 3 regressions: search score floor + file path (T5-rem) =====


def test_search_min_score_drops_negative_noise(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--min-score (default 0.0) drops low-score hits below the floor.

    Scores are now unified to [0,1] across all modes. The default floor of 0.0
    drops weak hits; --min-score 0.5 keeps only the stronger half.
    """
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    weak = mcp_v2.SearchHit(
        chunk_id="c1", symbol_id="sym1", fqn="com.example.Weak",
        score=0.1, snippet="weak", microservice="chat-core",
    )
    signal = mcp_v2.SearchHit(
        chunk_id="c2", symbol_id="sym2", fqn="com.example.Signal",
        score=0.6, snippet="signal", microservice="chat-core",
    )

    def make_mock(hits):
        def mock_search_v2(query, **kwargs):
            return mcp_v2.SearchOutput(
                success=True, results=hits,
                limit=kwargs.get("limit", 5), offset=kwargs.get("offset", 0),
                advisories=[],
            )
        return mock_search_v2

    # Default floor (0.0): both survive (both are ≥ 0).
    monkeypatch.setattr(mcp_v2, "search_v2", make_mock([weak, signal]))
    rc = main(["search", "--index-dir", env_index, "q", "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0, out
    payload = json.loads(out)
    fqns = {n.get("fqn") for n in payload.get("nodes", {}).values()}
    assert "com.example.Signal" in fqns
    assert "com.example.Weak" in fqns, "weak hit (0.1) should survive default floor 0.0"

    # Floor 0.5: only the strong signal survives.
    monkeypatch.setattr(mcp_v2, "search_v2", make_mock([weak, signal]))
    rc = main(["search", "--index-dir", env_index, "q", "--min-score", "0.5", "--format", "json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    fqns = {n.get("fqn") for n in payload.get("nodes", {}).values()}
    assert "com.example.Signal" in fqns
    assert "com.example.Weak" not in fqns, "weak hit (0.1) must be dropped by floor 0.5"


def test_search_hit_carries_file_path(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Each rendered search hit includes a `file` locator (filename:start_line)
    so an agent can jump to the hit. SearchHit now carries filename/start_line;
    the projector folds them into the `file` display field."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    hit = mcp_v2.SearchHit(
        chunk_id="c1", symbol_id="sym1", fqn="com.example.Hit",
        score=0.9, snippet="s", microservice="chat-core",
        filename="src/main/java/Hit.java", start_line=42,
    )
    def mock_search_v2(query, **kwargs):
        return mcp_v2.SearchOutput(
            success=True, results=[hit],
            limit=kwargs.get("limit", 5), offset=kwargs.get("offset", 0),
            advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "q", "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0, out
    payload = json.loads(out)
    node = next(iter(payload.get("nodes", {}).values()))
    assert node.get("file") == "src/main/java/Hit.java:42", (
        f"expected composed file locator, got {node.get('file')!r}"
    )

    # Text mode surfaces it inline as file=...
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)
    rc = main(["search", "--index-dir", env_index, "q"])
    out = capsys.readouterr().out
    assert "file=src/main/java/Hit.java:42" in out, f"expected file= in text output:\n{out}"


# ===== Phase 3 regressions: conventions scope + overview validation (T8/T5) =====


def test_conventions_service_scopes_frameworks(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """conventions --service scopes BOTH roles and frameworks.

    Previously --service narrowed the role tally but the route-framework tally
    was global (half-scoped output). Phase 3 T8 forwards --service to the
    framework query too, so a single-service scope shows fewer frameworks than
    the whole-fleet view.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc_global = _run_jrag(["conventions", "--format", "json"], env=env)
    assert proc_global.returncode == 0
    global_fw = json.loads(proc_global.stdout)["nodes"]["conventions"]["frameworks"]

    proc_scoped = _run_jrag(["conventions", "--service", "chat-assign", "--format", "json"], env=env)
    assert proc_scoped.returncode == 0, f"conventions --service failed: {proc_scoped.stderr}"
    scoped_fw = json.loads(proc_scoped.stdout)["nodes"]["conventions"]["frameworks"]

    # The scoped framework tallies must be <= the global ones per framework and
    # strictly smaller in total (chat-assign is a strict subset of the fleet).
    total_global = sum(global_fw.values())
    total_scoped = sum(scoped_fw.values())
    assert total_scoped < total_global, (
        f"scoped frameworks ({scoped_fw}) should total less than global ({global_fw})"
    )
    for fw, n in scoped_fw.items():
        assert n <= global_fw.get(fw, 0), f"scoped {fw}={n} exceeds global {global_fw.get(fw)}"


def test_overview_unknown_service_errors(corpus_root: Path, ladybug_db_path: Path) -> None:
    """overview --service <bogus> surfaces 'unknown microservice' (not a silent
    empty bundle). --service is also wired as the subject when no positional is
    given, so `overview --service <valid>` works like `overview <valid>`."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["overview", "--service", "bogus-service", "--format", "json"], env=env)
    assert proc.returncode == 2, (
        f"overview --service <bogus> should error: rc={proc.returncode}\nstdout={proc.stdout}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert "unknown microservice" in payload.get("message", ""), (
        f"expected 'unknown microservice' message, got {payload.get('message')!r}"
    )

    # Valid service with no positional still works (subject defaults to service).
    proc_ok = _run_jrag(["overview", "--service", "chat-assign", "--format", "json"], env=env)
    assert proc_ok.returncode == 0, f"overview --service chat-assign failed: {proc_ok.stderr}"
    payload_ok = json.loads(proc_ok.stdout)
    assert payload_ok["status"] == "ok"


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


def test_next_actions_skips_self_command_in_fallback() -> None:
    """Regression (review finding E): the result_edges fallback must not emit a
    self-hint (the command just run).

    After `callers`, the CALLS edges present would yield ``jrag callers`` (self)
    + ``jrag callees`` (inverse). Only the inverse is useful — the self-hint is
    dropped when ``current_command`` is supplied (the inverse remains).
    """
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.Foo"
    result_edges = [{"other_id": "a", "edge_type": "CALLS"}]
    # Without current_command: both directions (back-comat with test 16).
    both = next_actions(root_fqn=fqn, edge_summary=None, result_edges=result_edges)
    assert f"jrag callers {fqn}" in both, f"CALLS in missing: {both}"
    assert f"jrag callees {fqn}" in both, f"CALLS out missing: {both}"
    # With current_command="callers": self dropped, inverse kept.
    skipped = next_actions(
        root_fqn=fqn,
        edge_summary=None,
        result_edges=result_edges,
        current_command="callers",
    )
    assert f"jrag callers {fqn}" not in skipped, f"self-hint not skipped: {skipped}"
    assert f"jrag callees {fqn}" in skipped, f"inverse hint dropped: {skipped}"


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


# ===== Tests 17c–17g: Phase 2 — root-kind-aware hints (T4) =====
#
# Pins the corrected behavior: a route root never suggests `callees` (the kind
# guard rejects it), DECLARES_CLIENT/DECLARES_PRODUCER map to `callees`, and the
# per-kind allowlist filters invalid commands for Client/Producer roots.


def test_next_actions_route_root_emits_flow_not_callees() -> None:
    """A route root gets [flow, inspect] — never `callees` (Phase 2 T4).

    The label path would map HTTP_CALLS → `callees`, but `_cmd_callees` rejects
    route roots (kind guard). The route special-case emits `flow` (the natural
    escalation: full inbound+outbound chain) + `inspect`, minus current_command.
    """
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "POST /chat/joinOperator"
    # result_edges carry HTTP_CALLS (callers on a route) — would naively map to
    # `callees` out, which is invalid on a route root.
    result_edges = [{"other_id": "c1", "edge_type": "HTTP_CALLS"}]
    hints = next_actions(
        root_fqn=fqn, result_edges=result_edges,
        current_command="callers", root_kind="route",
    )
    assert f"jrag flow {fqn}" in hints, f"route root missing `flow`: {hints}"
    assert f"jrag inspect {fqn}" in hints, f"route root missing `inspect`: {hints}"
    assert f"jrag callees {fqn}" not in hints, (
        f"route root must NOT suggest `callees` (kind guard rejects it): {hints}"
    )


def test_next_actions_route_root_after_flow_drops_flow() -> None:
    """After `flow` on a route, only `inspect` remains (current_command dropped)."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "POST /chat/joinOperator"
    hints = next_actions(
        root_fqn=fqn, result_edges=[],
        current_command="flow", root_kind="route",
    )
    assert hints == [f"jrag inspect {fqn}"], (
        f"route after flow should only suggest inspect: {hints}"
    )


def test_next_actions_declares_client_maps_to_callees() -> None:
    """DECLARES_CLIENT (and the composed DECLARES.DECLARES_CLIENT form) → `callees`.

    A Symbol that declares a Client has a useful `callees` surface: `callees` on
    a CLIENT-role Symbol aggregates the declared Client's HTTP_CALLS targets.
    """
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.ChatCoreFeignClient"
    # Composed form (as produced by describe_v2's edge_summary rollup).
    hints = next_actions(
        root_fqn=fqn,
        edge_summary={"DECLARES.DECLARES_CLIENT": {"in": 0, "out": 2}},
        result_edges=[],
    )
    assert f"jrag callees {fqn}" in hints, (
        f"DECLARES.DECLARES_CLIENT should map to `callees`: {hints}"
    )
    # Plain form (defensive — if the label ever appears un-split).
    hints2 = next_actions(
        root_fqn=fqn,
        edge_summary={"DECLARES_CLIENT": {"in": 0, "out": 1}},
        result_edges=[],
    )
    assert f"jrag callees {fqn}" in hints2, (
        f"DECLARES_CLIENT should map to `callees`: {hints2}"
    )


def test_next_actions_declares_producer_maps_to_callees() -> None:
    """DECLARES_PRODUCER → `callees` (analogous to DECLARES_CLIENT for producers)."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.ChatEventPublisher"
    hints = next_actions(
        root_fqn=fqn,
        edge_summary={"DECLARES.DECLARES_PRODUCER": {"in": 0, "out": 1}},
        result_edges=[],
    )
    assert f"jrag callees {fqn}" in hints, (
        f"DECLARES.DECLARES_PRODUCER should map to `callees`: {hints}"
    )


def test_next_actions_client_kind_allowlist_filters_invalid_commands() -> None:
    """A Client-kind root filters out commands outside {callees, inspect}.

    The allowlist ensures a root never suggests a command whose kind guard would
    reject it. A Client root with CALLS edges (which would naively yield
    `callers` + `callees`) only surfaces `callees`.
    """
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "some.client.id"
    # CALLS would naively map to both `callers` (in) and `callees` (out).
    hints = next_actions(
        root_fqn=fqn,
        edge_summary={"CALLS": {"in": 3, "out": 2}},
        result_edges=[],
        root_kind="client",
    )
    assert f"jrag callees {fqn}" in hints, f"client root missing `callees`: {hints}"
    assert f"jrag callers {fqn}" not in hints, (
        f"client root must NOT suggest `callers` (filtered by allowlist): {hints}"
    )


def test_next_actions_symbol_root_unfiltered() -> None:
    """Symbol roots (and unknown/None root_kind) apply NO allowlist filtering."""
    from java_codebase_rag.jrag_hints import next_actions

    fqn = "com.example.Svc"
    hints = next_actions(
        root_fqn=fqn,
        edge_summary={"CALLS": {"in": 3, "out": 2}},
        result_edges=[],
        root_kind="symbol",
    )
    assert f"jrag callers {fqn}" in hints, f"symbol root missing `callers`: {hints}"
    assert f"jrag callees {fqn}" in hints, f"symbol root missing `callees`: {hints}"
    # None root_kind (back-comat: caller didn't pass it) → also unfiltered.
    hints2 = next_actions(
        root_fqn=fqn,
        edge_summary={"CALLS": {"in": 3, "out": 2}},
        result_edges=[],
    )
    assert f"jrag callers {fqn}" in hints2


# ===== Test 17a/17b: e2e hook wiring on real inspect =====

# Seed FQN verified against the bank-chat fixture: resolves to "one" and carries
# INJECTS edges (ChatManagementService injects repositories and is injected by
# controllers). See test_jrag_traversal_direct.py for resolve verification.
_SEED_CLASS_FQN = "com.bank.chat.assign.service.ChatManagementService"


def test_inspect_populates_agent_next_actions_json(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """e2e: `jrag inspect <fqn> --format json` populates agent_next_actions.

    Tests the full hook wiring: resolve → describe_v2 → edge_summary → hook →
    jrag_hints.next_actions → envelope.agent_next_actions. The unit tests
    (13–17) test the mapper directly; this verifies the fqn extraction from
    envelope.nodes[root] and the synthetic-kind guard in the hook.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["inspect", _SEED_CLASS_FQN, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"inspect failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    actions = payload.get("agent_next_actions", [])
    assert actions, (
        f"agent_next_actions empty — hook wiring broken: {payload}"
    )
    # At least one hint must be `jrag <cmd> <fqn>`.
    fqn = _SEED_CLASS_FQN
    found_runnable = any(
        a.startswith("jrag ") and a.endswith(fqn) for a in actions
    )
    assert found_runnable, f"no `jrag <cmd> {fqn}` in actions: {actions}"


def test_inspect_renders_next_actions_in_text(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """e2e: `jrag inspect <fqn>` (text mode) renders `next:` hint lines.

    After Fix 1, the inspect text renderer appends up to 2 `next: <hint>` lines
    when agent_next_actions is non-empty. This test verifies the text rendering
    path (the JSON path is covered by the test above).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["inspect", _SEED_CLASS_FQN], env=env)
    assert proc.returncode == 0, (
        f"inspect (text) failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "next:" in proc.stdout, (
        f"expected `next:` hint line in text output, got:\n{proc.stdout}"
    )


# ===== Test 18: build_parser lazy-import sentinel =====


def test_build_parser_imports_no_backend_modules() -> None:
    """build_parser() imports NO backend modules (torch / sentence_transformers / mcp_v2).

    Pins the lazy-import invariant: `jrag --help` stays fast and free of heavy
    deps. Uses a snapshot-diff approach: snapshot sys.modules keys before and
    after build_parser(), then assert the delta contains no heavy modules. This
    is robust under same-session pre-load pollution (other tests may have
    already imported heavy deps; we only care that build_parser doesn't ADD
    them).
    """
    heavy = {"torch", "sentence_transformers", "mcp_v2", "ladybug_queries", "resolve_service"}

    # Snapshot module keys before build_parser().
    before = set(sys.modules.keys())

    from java_codebase_rag.jrag import build_parser
    build_parser()

    # Delta = modules added by build_parser().
    after = set(sys.modules.keys())
    added = after - before
    leaked = added & heavy
    assert not leaked, (
        f"build_parser() imported backend module(s): {sorted(leaked)} — "
        "lazy-import invariant broken"
    )

    # Verify the parser lists the new commands.
    parser = build_parser()
    # The subparsers' actions include the subcommand dest.
    sub_actions = [a for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict)]  # noqa: SLF001
    if sub_actions:
        commands = set(sub_actions[0].choices.keys())
        for expected in ("microservices", "map", "conventions", "overview", "search"):
            assert expected in commands, f"missing {expected} in parser subcommands: {commands}"


def test_search_explain_calls_search_v2_with_explain_true(
    monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--explain flag passes explain=True to search_v2."""
    from java_codebase_rag.mcp import mcp_v2
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

    rc = main(["search", "--index-dir", env_index, "audit", "--explain", "--format", "json"])
    assert rc == 0
    assert captured_kwargs.get("explain") is True, (
        f"expected explain=True, got explain={captured_kwargs.get('explain')}"
    )


def test_search_dedup_default_collapses_same_fqn(monkeypatch, corpus_root: Path, ladybug_db_path: Path) -> None:
    """Default search (dedup ON) collapses multiple chunks of same FQN into one node."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    def mock_search_v2(query, **kwargs):
        # By default, dedup should be True
        assert kwargs.get("dedup") is True, f"expected dedup=True by default, got {kwargs.get('dedup')}"
        # Return 2 hits with same FQN
        return mcp_v2.SearchOutput(
            success=True,
            results=[
                mcp_v2.SearchHit(
                    chunk_id="chunk:1",
                    fqn="com.example.TypeA",
                    score=0.95,
                    snippet="TypeA chunk 1",
                    filename="a.java",
                    start_line=10,
                ),
                mcp_v2.SearchHit(
                    chunk_id="chunk:2",
                    fqn="com.example.TypeA",
                    score=0.85,
                    snippet="TypeA chunk 2",
                    filename="a.java",
                    start_line=20,
                ),
            ],
            limit=10,
            offset=0,
            advisories=[],
        )

    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)
    rc = main(["search", "--index-dir", env_index, "TypeA", "--format", "json"])
    assert rc == 0
    # The output should show the dedup behavior
    # (In real run, the 2 chunks would be collapsed to 1 node by run_search dedup)


def test_search_chunks_flag_passes_dedup_false(monkeypatch, corpus_root: Path, ladybug_db_path: Path) -> None:
    """--chunks flag passes dedup=False to search_v2, disabling dedup."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    captured_kwargs: dict = {}
    def mock_search_v2(query, **kwargs):
        captured_kwargs.update(kwargs)
        captured_kwargs["query"] = query
        # Return 2 hits with same FQN
        return mcp_v2.SearchOutput(
            success=True,
            results=[
                mcp_v2.SearchHit(
                    chunk_id="chunk:1",
                    fqn="com.example.TypeA",
                    score=0.95,
                    snippet="TypeA chunk 1",
                    filename="a.java",
                    start_line=10,
                ),
                mcp_v2.SearchHit(
                    chunk_id="chunk:2",
                    fqn="com.example.TypeA",
                    score=0.85,
                    snippet="TypeA chunk 2",
                    filename="a.java",
                    start_line=20,
                ),
            ],
            limit=10,
            offset=0,
            advisories=[],
        )

    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)
    rc = main(["search", "--index-dir", env_index, "TypeA", "--chunks", "--format", "json"])
    assert rc == 0
    assert captured_kwargs.get("dedup") is False, f"expected dedup=False with --chunks, got {captured_kwargs.get('dedup')}"


def test_search_limit_zero_returns_empty_not_truncated(monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path) -> None:
    """--limit 0 returns clean empty page (truncated:false) WITHOUT calling search_v2."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    search_v2_calls = []
    def mock_search_v2(query, **kwargs):
        search_v2_calls.append({"query": query, "kwargs": kwargs})
        return mcp_v2.SearchOutput(
            success=True, results=[], limit=kwargs.get("limit", 5),
            offset=kwargs.get("offset", 0), advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "anything", "--limit", "0", "--format", "json"])
    captured = capsys.readouterr()
    assert rc == 0, f"search failed: rc={rc}\nstdout={captured.out}\nstderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["status"] == "ok", f"expected status=ok, got {payload.get('status')}"
    assert payload.get("truncated") is not True, f"expected truncated not true (the --limit-0 bug surfaced truncated:true), got {payload.get('truncated')}"
    assert not payload.get("nodes"), f"expected empty nodes, got {payload.get('nodes')}"
    assert len(search_v2_calls) == 0, f"search_v2 should NOT be called with --limit 0, but was called {len(search_v2_calls)} times"


def test_search_zero_results_with_role_filter_emits_guidance(monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path) -> None:
    """Filtered search returning 0 results emits guidance when matches exist under other filter values."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    # Mock search_v2 to track calls and return empty for filtered, hits for unfiltered probe
    search_v2_calls = []
    def mock_search_v2(query, **kwargs):
        search_v2_calls.append({"query": query, "kwargs": kwargs})
        # If no filter (probe), return hits with various roles
        if kwargs.get("filter") is None or (
            isinstance(kwargs.get("filter"), dict) and not kwargs.get("filter")
        ):
            return mcp_v2.SearchOutput(
                success=True,
                results=[
                    mcp_v2.SearchHit(
                        chunk_id="c1", symbol_id="sym1", fqn="com.example.HitA",
                        score=0.95, snippet="audit code A", microservice="ms1",
                        module="m1", role="COMPONENT",
                    ),
                    mcp_v2.SearchHit(
                        chunk_id="c2", symbol_id="sym2", fqn="com.example.HitB",
                        score=0.85, snippet="audit code B", microservice="ms2",
                        module="m2", role="OTHER",
                    ),
                    mcp_v2.SearchHit(
                        chunk_id="c3", symbol_id="sym3", fqn="com.example.HitC",
                        score=0.75, snippet="audit code C", microservice="ms3",
                        module="m3", role="COMPONENT",
                    ),
                ],
                limit=10,
                offset=0,
                advisories=[],
            )
        # Filtered call returns empty
        return mcp_v2.SearchOutput(
            success=True, results=[], limit=kwargs.get("limit", 5),
            offset=kwargs.get("offset", 0), advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "audit", "--role", "SERVICE", "--format", "json"])
    captured = capsys.readouterr()
    assert rc == 0, f"search failed: rc={rc}\nstdout={captured.out}\nstderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    # Should have warning mentioning alternative roles (COMPONENT should be mentioned)
    warnings = payload.get("warnings", [])
    assert len(warnings) > 0, "expected guidance warning when filtered search returns 0 but unfiltered has results"
    warning_text = " ".join(warnings)
    assert "COMPONENT" in warning_text, f"expected COMPONENT in warning, got: {warning_text}"
    assert "0 results with --role SERVICE" in warning_text, f"expected filter context in warning, got: {warning_text}"
    # Verify probe was called (unfiltered)
    assert len(search_v2_calls) == 2, f"expected 2 calls (filtered + probe), got {len(search_v2_calls)}"


def test_search_zero_results_no_filter_no_guidance(monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path) -> None:
    """Unfiltered search returning 0 results does NOT run probe or emit guidance."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    search_v2_calls = []
    def mock_search_v2(query, **kwargs):
        search_v2_calls.append({"query": query, "kwargs": kwargs})
        return mcp_v2.SearchOutput(
            success=True, results=[], limit=kwargs.get("limit", 5),
            offset=kwargs.get("offset", 0), advisories=[],
        )
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "nonexistent query xyz", "--format", "json"])
    captured = capsys.readouterr()
    assert rc == 0, f"search failed: rc={rc}\nstdout={captured.out}\nstderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    warnings = payload.get("warnings", [])
    # Should NOT have guidance warning for unfiltered empty search
    guidance_warnings = [w for w in warnings if "results with --" in w or "try --" in w]
    assert len(guidance_warnings) == 0, f"should not run probe for unfiltered empty search, got warnings: {warnings}"
    # Should only be called once (no probe)
    assert len(search_v2_calls) == 1, f"expected 1 call (no probe for unfiltered), got {len(search_v2_calls)}"


def test_search_probe_failure_does_not_break_empty_rendering(monkeypatch, capsys, corpus_root: Path, ladybug_db_path: Path) -> None:
    """When probe fails (raises), the empty result still renders successfully."""
    from java_codebase_rag.mcp import mcp_v2
    from java_codebase_rag.jrag import main

    env_index = str(ladybug_db_path.parent)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", env_index)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", str(corpus_root))

    call_count = []
    def mock_search_v2(query, **kwargs):
        call_count.append(kwargs)
        # First call (filtered): returns empty
        if len(call_count) == 1:
            return mcp_v2.SearchOutput(
                success=True, results=[], limit=kwargs.get("limit", 5),
                offset=kwargs.get("offset", 0), advisories=[],
            )
        # Second call (probe): raises exception
        raise RuntimeError("probe failure (network/db error)")
    monkeypatch.setattr(mcp_v2, "search_v2", mock_search_v2)

    rc = main(["search", "--index-dir", env_index, "audit", "--role", "SERVICE", "--format", "json"])
    captured = capsys.readouterr()
    # Should still succeed (probe failure is non-fatal)
    assert rc == 0, f"search should succeed despite probe failure, got rc={rc}\nstdout={captured.out}\nstderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    # Should still show empty results (nodes omitted when empty)
    assert not payload.get("nodes")
