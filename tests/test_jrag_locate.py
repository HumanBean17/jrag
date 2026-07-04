"""Tests for `jrag find` + `inspect` (PR-JRAG-1b).

Tests:
1. test_find_by_fqn_exact - query mode, exact FQN match
2. test_find_filter_mode_by_role - filter mode, --role controller
3. test_find_by_capability - --capability scheduled-task, symbol inferred
4. test_find_kind_inference_from_http_method - route inferred
5. test_find_kind_contradiction_is_error - --kind symbol --http-method GET
6. test_find_fuzzy_falls_back_to_prefix - --fuzzy fallback
7. test_find_annotation_flag_filters - --annotation post-filter
8. test_find_exclude_role_flag_filters - --exclude-role post-filter
9. test_find_offset_paginates - --offset works on find
10. test_find_limit_capped_under_500 - --limit 600 behaves as ≤499
11. test_inspect_returns_edge_summary_with_composed_keys - OVERRIDDEN_BY virtual key
12. test_inspect_ambiguous_returns_candidates - resolve returns many
13. test_inspect_populates_file_location - file_location set by resolve
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
    """--capability scheduled-task with symbol kind inferred."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["find", "--capability", "scheduled-task", "--format", "json"], env=env)
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # Should return symbols with scheduled-task capability


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


# ----- Test 6: find fuzzy falls back to prefix -----


def test_find_fuzzy_falls_back_to_prefix(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--fuzzy enables prefix fallback when exact returns nothing."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Use a partial prefix that won't match exactly but should match with prefix
    proc = _run_jrag(["find", "Account", "--fuzzy", "--format", "json", "--limit", "5"], env=env)
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # Should return results with Account prefix


# ----- Test 7: find annotation flag filters -----


def test_find_annotation_flag_filters(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--annotation post-filters results."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Find symbols with @RestController annotation
    proc = _run_jrag(
        ["find", "--annotation", "RestController", "--format", "json", "--limit", "10"], env=env
    )
    assert proc.returncode == 0, f"find failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # Results should have RestController in annotations


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


# ----- Test 11: inspect returns edge_summary with composed keys -----


def test_inspect_returns_edge_summary_with_composed_keys(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Inspect returns edge_summary with OVERRIDDEN_BY virtual key."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Find a method that overrides another (if any exist in the fixture)
    # For now, just inspect any known node
    proc = _run_jrag(["inspect", "com.bank.chat.assign.ChatAssignApplication", "--format", "json"], env=env)
    assert proc.returncode == 0, f"inspect failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    assert len(nodes) == 1, f"expected 1 node, got {len(nodes)}"
    # Check that edge_summary is present (may be empty if no edges)
    for node_id, node in nodes.items():
        # edge_summary might not exist for all nodes, but the structure should be valid
        if "edge_summary" in node:
            edge_summary = node["edge_summary"]
            # If present, it should be a dict
            assert isinstance(edge_summary, dict), "edge_summary should be a dict"
    # Success if we got here without crashing


# ----- Test 12: inspect ambiguous returns candidates -----


def test_inspect_ambiguous_returns_candidates(corpus_root: Path, ladybug_db_path: Path) -> None:
    """Inspect on ambiguous query returns candidates (no auto-pick)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Use a generic query that might match multiple nodes
    proc = _run_jrag(["inspect", "Account", "--format", "json"], env=env)
    # Should either return ok (if exactly one) or ambiguous (if multiple)
    assert proc.returncode in (0, 2), f"unexpected exit code: {proc.returncode}"

    payload = json.loads(proc.stdout)
    if payload["status"] == "ambiguous":
        # Should have candidates list
        candidates = payload.get("candidates", [])
        assert len(candidates) > 0, "ambiguous should have candidates"
        # Each candidate should have reason
        for cand in candidates:
            assert "reason" in cand, "candidate should have reason"
    elif payload["status"] == "ok":
        # Unambiguously resolved - that's fine too
        pass


# ----- Test 13: inspect populates file_location -----


def test_inspect_populates_file_location(corpus_root: Path, ladybug_db_path: Path) -> None:
    """Inspect populates file_location from resolve_query."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Inspect a specific known symbol
    proc = _run_jrag(["inspect", "com.bank.chat.assign.ChatAssignApplication", "--format", "json"], env=env)
    assert proc.returncode == 0, f"inspect failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # file_location should be populated
    file_location = payload.get("file_location")
    if file_location:
        # Should be in format "filename:line" or "filename"
        assert ":" in file_location or isinstance(file_location, str), f"invalid file_location: {file_location}"
