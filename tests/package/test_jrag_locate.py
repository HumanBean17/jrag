"""Tests for `jrag find` + `inspect` (PR-JRAG-1b).

Tests:
1. test_find_by_fqn_exact - query mode, exact FQN match
2. test_find_filter_mode_by_role - filter mode, --role controller
3. test_find_by_capability - --capability scheduled-task, symbol inferred
4. test_find_kind_inference_from_http_method - route inferred
5. test_find_kind_contradiction_is_error - --kind symbol --http-method GET
6. test_find_query_mode_with_non_symbol_kind_returns_error - query mode + route/client/producer errors
7. test_find_annotation_flag_filters - --annotation post-filter
8. test_find_exclude_role_flag_filters - --exclude-role post-filter
9. test_find_offset_paginates - --offset works on find
10. test_find_limit_capped_under_500 - --limit 600 behaves as ≤499
11. test_find_query_mode_framework_and_source_layer_warn - dropped filters warn
12. test_inspect_returns_edge_summary_with_composed_keys - OVERRIDDEN_BY virtual key
13. test_inspect_ambiguous_returns_candidates - resolve returns many
14. test_inspect_populates_file_location - file_location set by resolve
15. test_find_fuzzy_returns_results_when_exact_misses - --fuzzy prefix/substring fallback
16. test_find_fuzzy_exact_match_skips_fallback - exact hit does not trigger fallback
17. test_find_fuzzy_no_match_reports_tried_modes - gibberish notes all three modes tried
18. test_find_empty_without_fuzzy_suggests_flag - empty exact result suggests --fuzzy
19. test_find_fuzzy_match_removed_by_postfilter_blames_filter - fuzzy hit emptied by --role blames filter
20. test_find_fuzzy_forwards_scope_filters - --service flows into the fuzzy fallback tiers

Note: --fuzzy is implemented as an exact -> prefix -> substring fallback on
name/FQN (issue #375); fuzzy modes exclude file/package Symbol nodes (#411).
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


# ----- Test 1: find by FQN exact (query mode) -----


def test_find_by_fqn_exact(corpus_root: Path, ladybug_db_path: Path) -> None:
    """Query mode: exact FQN match returns the node."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Find a known class from the bank-chat fixture
    proc = _run_jrag(["find", "com.bank.chat.assign.ChatAssignApplication", "--format", "json"], env=env)
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    assert len(nodes) >= 1, f"expected at least one node, got {len(nodes)}"
    # The exact match should be in the results
    for node_id, node in nodes.items():
        if "ChatAssignApplication" in node.get("fqn", ""):
            assert "ChatAssignApplication" in node.get("fqn", "")
            return
    assert False, "ChatAssignApplication class not found in results"


# ----- Test 2: find filter mode by role -----


def test_find_filter_mode_by_role(corpus_root: Path, ladybug_db_path: Path) -> None:
    """Filter mode: --role controller returns only controllers."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["find", "--role", "controller", "--format", "json"], env=env)
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # All nodes should have role=CONTROLLER (normalized)
    for node_id, node in nodes.items():
        role = node.get("role", "").upper()
        assert role == "CONTROLLER", f"expected CONTROLLER role, got {role}"


# ----- Test 3: find by capability (symbol inferred) -----


def test_find_by_capability(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--capability scheduled-task narrows vs. unfiltered (a real filter, not a no-op).

    The prior test asserted only ``status == 'ok'`` — it would pass even if
    ``--capability`` were silently ignored. Now: the filtered set must be a
    subset of all symbols, and a STRICT subset when the fixture has any
    scheduled-task symbols.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc_all = _run_jrag(["find", "--limit", "499", "--format", "json"], env=env)
    assert proc_all.returncode == 0, f"find (all) failed: {proc_all.stderr}"
    all_count = len(json.loads(proc_all.stdout).get("nodes", {}))

    proc = _run_jrag(
        ["find", "--capability", "scheduled-task", "--limit", "499", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # Filtered set must not exceed the unfiltered set.
    assert len(nodes) <= all_count, (
        f"--capability did not narrow: filtered={len(nodes)} > all={all_count}"
    )
    for node in nodes.values():
        assert node.get("kind") == "symbol", f"--capability returned non-symbol: {node.get('kind')}"
    # If the fixture has any scheduled-task symbols, the filter is a STRICT subset
    # (proving the capability predicate was applied, not ignored).
    if len(nodes) > 0 and all_count > 0:
        assert len(nodes) < all_count, (
            f"--capability returned the full set ({len(nodes)} == {all_count}); filter is a no-op"
        )


# ----- Test 4: find kind inference from http_method -----


def test_find_kind_inference_from_http_method(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--http-method GET implies kind=route."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(
        ["find", "--http-method", "GET", "--format", "json", "--limit", "5"], env=env
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # All nodes should be routes
    for node_id, node in nodes.items():
        kind = node.get("kind", "")
        assert kind == "route", f"expected kind=route, got {kind}"


# ----- Test 5: find kind contradiction is error -----


def test_find_kind_contradiction_is_error(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--kind symbol --http-method GET returns error envelope (contradiction)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["find", "--kind", "symbol", "--http-method", "GET", "--format", "json"], env=env)
    assert proc.returncode == 2, f"expected error exit code, got {proc.returncode}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert "contradiction" in payload.get("message", "").lower() or "conflict" in payload.get("message", "").lower()


# ----- Test 6: find query mode + non-symbol kind errors -----


def test_find_query_mode_with_non_symbol_kind_returns_error(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Query mode (positional <query>) + non-symbol kind -> status: error.

    find_by_name_or_fqn is Symbol-only (exact name/FQN match). A positional
    <query> with explicit --kind route OR a domain flag that infers a non-symbol
    kind (--http-method -> route) must error (NOT silently return empty),
    telling the user to drop the positional and use filter mode.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Explicit --kind route + positional query
    proc = _run_jrag(["find", "--kind", "route", "SomeQuery", "--format", "json"], env=env)
    assert proc.returncode == 2, f"explicit route: expected exit 2, got {proc.returncode}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error", f"explicit route: {payload}"
    msg = payload.get("message", "")
    assert "Symbol" in msg, f"explicit route msg should mention Symbol: {msg!r}"
    assert "filter mode" in msg, f"explicit route msg should mention filter mode: {msg!r}"

    # Inferred route (--http-method) + positional query
    proc = _run_jrag(
        ["find", "--http-method", "GET", "SomeName", "--format", "json"], env=env
    )
    assert proc.returncode == 2, f"inferred route: expected exit 2, got {proc.returncode}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error", f"inferred route: {payload}"
    assert "Symbol" in payload.get("message", ""), "inferred route should mention Symbol"


# ----- Test 7: find annotation flag filters -----


def test_find_annotation_flag_filters(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--annotation narrows vs. unfiltered (a real filter, not a no-op).

    The prior test asserted only ``status == 'ok'``. Now: the annotated set must
    be a subset, and a strict subset when the fixture has any @RestController.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc_all = _run_jrag(["find", "--limit", "499", "--format", "json"], env=env)
    assert proc_all.returncode == 0, f"find (all) failed: {proc_all.stderr}"
    all_count = len(json.loads(proc_all.stdout).get("nodes", {}))

    # Find symbols with @RestController annotation
    proc = _run_jrag(
        ["find", "--annotation", "RestController", "--limit", "499", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    assert len(nodes) <= all_count, (
        f"--annotation did not narrow: filtered={len(nodes)} > all={all_count}"
    )
    if len(nodes) > 0 and all_count > 0:
        assert len(nodes) < all_count, (
            f"--annotation returned the full set ({len(nodes)} == {all_count}); filter is a no-op"
        )


# ----- Test 8: find exclude_role flag filters -----


def test_find_exclude_role_flag_filters(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--exclude-role post-filters out nodes with that role."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Find symbols but exclude controllers
    proc = _run_jrag(
        ["find", "--exclude-role", "controller", "--format", "json", "--limit", "10"], env=env
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # No node should have role=CONTROLLER
    for node_id, node in nodes.items():
        role = node.get("role", "").upper()
        assert role != "CONTROLLER", f"found excluded CONTROLLER role in {node}"


# ----- Test 9: find offset paginates -----


def test_find_offset_paginates(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--offset works in filter mode (page 2 differs from page 1)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Get first page
    proc1 = _run_jrag(["find", "--format", "json", "--limit", "3", "--offset", "0"], env=env)
    assert proc1.returncode == 0
    page1 = json.loads(proc1.stdout)
    nodes1 = set(page1.get("nodes", {}).keys())

    # Get second page
    proc2 = _run_jrag(["find", "--format", "json", "--limit", "3", "--offset", "3"], env=env)
    assert proc2.returncode == 0
    page2 = json.loads(proc2.stdout)
    nodes2 = set(page2.get("nodes", {}).keys())

    # Pages should have different nodes (or page2 should be empty/shorter)
    if nodes1 and nodes2:
        assert nodes1 != nodes2, "pages should have different nodes"


# ----- Test 10: find limit capped under 500 -----


def test_find_limit_capped_under_500(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--limit 600 behaves as ≤499 (backend clamp)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["find", "--limit", "600", "--format", "json"], env=env)
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # Should return at most 500 results (capped at 499 limit + 1 for truncation check)
    # The backend clamp is at 500, so we should see ≤500 results
    assert len(nodes) <= 500, f"expected ≤500 results, got {len(nodes)}"


# ----- Test 11: find query mode framework/source-layer warn when dropped -----


def test_find_query_mode_framework_and_source_layer_warn(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--framework/--source-layer in query mode are dropped (SymbolHit lacks those
    fields) but surface a warnings[] entry so the user knows their filter had no effect.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(
        ["find", "com.bank.chat.assign.ChatAssignApplication", "--framework", "spring-mvc", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    warnings = payload.get("warnings", [])
    assert any("--framework" in w and "ignored" in w for w in warnings), (
        f"expected --framework ignored warning, got warnings={warnings}"
    )

    # --source-layer in query mode
    proc = _run_jrag(
        ["find", "com.bank.chat.assign.ChatAssignApplication", "--source-layer", "layer-a", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    warnings = payload.get("warnings", [])
    assert any("--source-layer" in w and "ignored" in w for w in warnings), (
        f"expected --source-layer ignored warning, got warnings={warnings}"
    )


# ----- Test 12: inspect returns edge_summary with composed keys -----


def test_inspect_returns_edge_summary_with_composed_keys(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Inspect returns edge_summary with the virtual OVERRIDDEN_BY composed key.

    The abstract port method ``ChatAssignmentPort#requestAssignment`` has one
    implementor in the bank-chat fixture (verified via test_mcp_v2_compose.
    test_describe_abstract_port_emits_overridden_by_rollup), so its
    edge_summary must carry ``OVERRIDDEN_BY = {"in": 0, "out": 1}``.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    method_fqn = "com.bank.chat.engine.assign.ChatAssignmentPort#requestAssignment(AssignmentRequest)"
    proc = _run_jrag(["inspect", method_fqn, "--format", "json"], env=env)
    assert proc.returncode == 0, f"inspect failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    nodes = payload.get("nodes", {})
    assert len(nodes) == 1, f"expected 1 node, got {len(nodes)}"
    node = next(iter(nodes.values()))
    edge_summary = node.get("edge_summary")
    assert isinstance(edge_summary, dict), f"edge_summary should be a dict, got {type(edge_summary)}"
    # The OVERRIDDEN_BY virtual composed key must be present with out > 0
    # (override_axis_rollup_for feeds this; see describe_v2 / mcp_v2_compose test).
    assert "OVERRIDDEN_BY" in edge_summary, (
        f"expected OVERRIDDEN_BY composed key, got keys={list(edge_summary.keys())}"
    )
    ob = edge_summary["OVERRIDDEN_BY"]
    assert int(ob.get("out", 0)) > 0, f"expected OVERRIDDEN_BY out > 0, got {ob}"


# ----- Test 12: inspect ambiguous returns candidates -----


def test_inspect_ambiguous_returns_candidates(corpus_root: Path, ladybug_db_path: Path) -> None:
    """Inspect on a query that may match multiple nodes returns `ambiguous` with
    candidates (no auto-pick). Each outcome asserts something real — the prior
    ``elif status == 'ok': pass`` made the test vacuous for the most common
    outcome (a clean resolve).
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Use a generic query that might match multiple nodes
    proc = _run_jrag(["inspect", "Account", "--format", "json"], env=env)
    # Should either return ok (if exactly one) or ambiguous (if multiple)
    assert proc.returncode in (0, 2), f"unexpected exit code: {proc.returncode}"

    payload = json.loads(proc.stdout)
    status = payload["status"]
    if status == "ambiguous":
        candidates = payload.get("candidates", [])
        assert len(candidates) > 0, "ambiguous must carry candidates"
        for cand in candidates:
            assert "reason" in cand, "candidate must carry reason"
    elif status == "ok":
        # A clean resolve must yield exactly ONE node (not a silent multi-return).
        assert len(payload.get("nodes", {})) == 1, (
            f"ok must mean a single resolved node, got {len(payload.get('nodes', {}))}: {payload}"
        )
    else:
        assert status == "not_found", f"unexpected status: {status}"
        assert payload.get("message"), "not_found must carry a message"


# ----- Test 12b: inspect --service disambiguates a cross-service name collision -----


def test_inspect_service_flag_disambiguates_collision(
    ladybug_db_path_route_extraction_smoke: Path,
) -> None:
    """--service is forwarded to resolve_query (regression: inspect used to
    silently ignore the inherited --service/--module flags).

    The route_extraction_smoke corpus has ``UserController`` as two DISTINCT
    types across services — ``smoke.a.UserController`` (service-a) and
    ``smoke.b.UserController`` (service-b). By simple name the resolve is
    ``ambiguous`` (two genuinely-different types; the class-vs-constructor
    auto-pick never collapses two distinct classes). Passing
    ``--service service-a`` must narrow resolve_v2 to smoke.a.UserController
    and yield ``ok`` with exactly that one resolved node.

    (The fqn_collision_smoke fixture does NOT work here: both copies share the
    SAME fqn ``com.example.SharedDto``, so the graph builder merges them into a
    single node and the baseline resolve is already ``ok``.)
    """
    fixture_root = Path(__file__).parent.parent / "fixtures" / "route_extraction_smoke"
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(fixture_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path_route_extraction_smoke.parent)

    # Baseline: by simple name, UserController is ambiguous across the two services.
    base = _run_jrag(["inspect", "UserController", "--format", "json"], env=env)
    assert base.returncode in (0, 2), f"baseline rc={base.returncode}\n{base.stdout}"
    base_payload = json.loads(base.stdout)
    assert base_payload["status"] == "ambiguous", (
        f"expected ambiguous baseline, got {base_payload['status']}: {base.stdout}"
    )
    assert len(base_payload.get("candidates", [])) > 1, (
        f"baseline must expose >1 candidate: {base.stdout}"
    )

    # Fix under test: --service narrows resolve_v2 to the service-a node.
    scoped = _run_jrag(
        ["inspect", "UserController", "--service", "service-a", "--format", "json"], env=env
    )
    assert scoped.returncode == 0, (
        f"inspect --service failed: rc={scoped.returncode}\nstdout={scoped.stdout}\nstderr={scoped.stderr}"
    )
    scoped_payload = json.loads(scoped.stdout)
    assert scoped_payload["status"] == "ok", (
        f"--service service-a should disambiguate to ok, got {scoped_payload['status']}: {scoped.stdout}"
    )
    nodes = scoped_payload.get("nodes", {})
    assert len(nodes) == 1, (
        f"expected exactly one resolved node, got {len(nodes)}: {scoped.stdout}"
    )
    resolved_fqn = next(iter(nodes.values())).get("fqn", "")
    assert resolved_fqn == "smoke.a.UserController", (
        f"--service service-a should resolve smoke.a.UserController, got {resolved_fqn}"
    )


# ----- Test 13: inspect populates file_location -----


def test_inspect_populates_file_location(corpus_root: Path, ladybug_db_path: Path) -> None:
    """Inspect populates file_location from resolve_query (filename:start_line)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Inspect a specific known symbol that resolves cleanly and has a file location.
    proc = _run_jrag(["inspect", "com.bank.chat.assign.ChatAssignApplication", "--format", "json"], env=env)
    assert proc.returncode == 0, f"inspect failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    # file_location is populated by resolve_query from the resolved node's
    # filename + start_line (jrag_envelope._node_file_location).
    file_location = payload.get("file_location")
    assert file_location is not None, "expected file_location to be populated for a real symbol"
    # Should be in format "filename:start_line" (start_line present for symbols).
    assert "ChatAssignApplication.java" in file_location, f"unexpected file_location: {file_location}"


# ----- Test 15-18: find --fuzzy (exact -> prefix -> substring fallback, #375) -----


def test_find_fuzzy_returns_results_when_exact_misses(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--fuzzy widens an empty exact match to prefix/substring on name/FQN."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Exact miss: no symbol is named exactly 'ChatManag'.
    proc_exact = _run_jrag(["find", "ChatManag", "--format", "json"], env=env)
    assert proc_exact.returncode == 0, proc_exact.stderr
    assert len(json.loads(proc_exact.stdout).get("nodes", {})) == 0

    # Fuzzy hit: prefix matches ChatManagementService.
    proc = _run_jrag(["find", "ChatManag", "--fuzzy", "--format", "json"], env=env)
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", payload
    nodes = payload.get("nodes", {})
    assert len(nodes) >= 1, payload
    assert any(
        n.get("name") == "ChatManagementService"
        or n.get("fqn", "").endswith(".ChatManagementService")
        for n in nodes.values()
    ), nodes
    # Fuzzy fallback is surfaced as a warning naming the matched mode.
    assert any("fuzzy" in w.lower() for w in payload.get("warnings", [])), payload


def test_find_fuzzy_exact_match_skips_fallback(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """When the exact name matches, --fuzzy is a no-op: no fallback warning."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(
        ["find", "ChatManagementService", "--fuzzy", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", payload
    assert len(payload.get("nodes", {})) >= 1, payload
    # Exact hit means no fuzzy fallback fired -> no fuzzy warning.
    assert not any("fuzzy" in w.lower() for w in payload.get("warnings", [])), payload


def test_find_fuzzy_no_match_reports_tried_modes(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Gibberish + --fuzzy: 0 nodes and a message noting all three modes were tried."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(
        ["find", "ZZZNoSuchSymbolXYZ", "--fuzzy", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", payload
    assert len(payload.get("nodes", {})) == 0, payload
    msg = (payload.get("message") or "").lower()
    assert "exact" in msg and "prefix" in msg and "substring" in msg, payload
    assert payload.get("agent_next_actions"), payload


def test_find_empty_without_fuzzy_suggests_flag(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """An empty exact result without --fuzzy points the user at --fuzzy."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["find", "ChatManag", "--format", "json"], env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", payload
    assert len(payload.get("nodes", {})) == 0, payload
    assert "--fuzzy" in (payload.get("message") or ""), payload


# ----- Test 19-20: find --fuzzy edge cases (review feedback) -----


def test_find_fuzzy_match_removed_by_postfilter_blames_filter(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Fuzzy matched the identifier, but --role removed every hit: the message
    must blame the filter, not claim 'tried exact, prefix, substring'.

    `ChatManag` prefix-matches ChatManagementController (CONTROLLER) and
    ChatManagementService (SERVICE), both in chat-assign. Neither is a REPOSITORY,
    so --role repository empties the set after the fuzzy tier already matched.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(
        ["find", "ChatManag", "--fuzzy", "--role", "repository", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", payload
    assert len(payload.get("nodes", {})) == 0, payload
    msg = (payload.get("message") or "").lower()
    assert "removed all hits" in msg, payload
    assert "tried exact, prefix, substring" not in msg, payload


def test_find_fuzzy_forwards_scope_filters(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--service must flow into the fuzzy fallback tiers, not just the exact fetch.

    `ChatManag` lives only in chat-assign; scoping the fuzzy fallback to chat-core
    must yield 0 (proving the scope filter reached the prefix/contains tiers),
    while scoping to chat-assign returns them, all within chat-assign.
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc_core = _run_jrag(
        ["find", "ChatManag", "--fuzzy", "--service", "chat-core", "--format", "json"],
        env=env,
    )
    payload_core = json.loads(proc_core.stdout)
    assert payload_core["status"] == "ok", payload_core
    assert len(payload_core.get("nodes", {})) == 0, payload_core

    proc_assign = _run_jrag(
        ["find", "ChatManag", "--fuzzy", "--service", "chat-assign", "--format", "json"],
        env=env,
    )
    payload_assign = json.loads(proc_assign.stdout)
    assert payload_assign["status"] == "ok", payload_assign
    nodes = payload_assign.get("nodes", {})
    assert len(nodes) >= 1, payload_assign
    assert all("chat.assign" in n.get("fqn", "") for n in nodes.values()), payload_assign


# ----- issue #376: --count / --exists / --fields end-to-end (exit-code contract) -----

_KNOWN_FQN = "com.bank.chat.assign.ChatAssignApplication"
_MISS_FQN = "com.bank.chat.assign.DoesNotExistGibberishXYZ123"


def _index_env(corpus_root: Path, ladybug_db_path: Path) -> dict[str, str]:
    """Env pointing jrag at the shared fixture index (DRY for the flag tests)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)
    return env


def test_find_exists_true_on_hit(corpus_root: Path, ladybug_db_path: Path) -> None:
    """find <existing> --exists -> 'true', exit 0."""
    proc = _run_jrag(["find", _KNOWN_FQN, "--exists"], env=_index_env(corpus_root, ladybug_db_path))
    assert proc.returncode == 0, f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert proc.stdout.strip() == "true"


def test_find_exists_false_on_miss_exits_2(corpus_root: Path, ladybug_db_path: Path) -> None:
    """find <missing> --exists -> 'false', exit 2 (the gating contract)."""
    proc = _run_jrag(["find", _MISS_FQN, "--exists"], env=_index_env(corpus_root, ladybug_db_path))
    assert proc.returncode == 2, f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert proc.stdout.strip() == "false"


def test_find_count_reports_bare_int_and_json(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--count text is a bare int (rc 0 even when >0); --format json shapes it."""
    env = _index_env(corpus_root, ladybug_db_path)
    proc_c = _run_jrag(["find", "--role", "controller", "--count"], env=env)
    assert proc_c.returncode == 0, f"rc={proc_c.returncode}\nstderr={proc_c.stderr}"
    n = int(proc_c.stdout.strip())
    assert n > 0, f"expected controllers in fixture, got count={n}"

    proc_j = _run_jrag(["find", "--role", "controller", "--count", "--format", "json"], env=env)
    payload = json.loads(proc_j.stdout)
    assert payload == {"status": "ok", "count": n}


def test_find_count_matches_row_count(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--count equals the number of node rows the same query returns without it."""
    env = _index_env(corpus_root, ladybug_db_path)
    proc_rows = _run_jrag(["find", "--role", "controller", "--format", "json"], env=env)
    assert proc_rows.returncode == 0, proc_rows.stderr
    row_count = len(json.loads(proc_rows.stdout).get("nodes", {}))

    proc_count = _run_jrag(["find", "--role", "controller", "--count"], env=env)
    assert int(proc_count.stdout.strip()) == row_count


def test_inspect_exists_false_on_miss_exits_2(corpus_root: Path, ladybug_db_path: Path) -> None:
    """inspect <missing> --exists routes the resolve-miss through the flag ->
    'false', exit 2 (exercises the _resolve/inspect not_found -> _emit path)."""
    proc = _run_jrag(["inspect", _MISS_FQN, "--exists"], env=_index_env(corpus_root, ladybug_db_path))
    assert proc.returncode == 2, f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert proc.stdout.strip() == "false"


def test_find_fields_projects_json_to_allowlist(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--fields fqn,role keeps only those keys on each node (overrides --detail)."""
    proc = _run_jrag(
        ["find", _KNOWN_FQN, "--format", "json", "--fields", "fqn,role"],
        env=_index_env(corpus_root, ladybug_db_path),
    )
    assert proc.returncode == 0, f"rc={proc.returncode}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok" and payload["nodes"]
    for node in payload["nodes"].values():
        assert set(node.keys()) <= {"fqn", "role"}, f"unexpected keys: {node.keys()}"
        assert "fqn" in node
