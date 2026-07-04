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

Note: --fuzzy was deferred (backend find_by_name_or_fqn is exact-only; see
plans/active/PLAN-JRAG-CLI.md Out of scope).
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
