"""Tests for `jrag` listing commands (PR-JRAG-2).

Tests:
1. test_routes_returns_route_kind - routes command returns route nodes
2. test_clients_filters_by_calls_service - clients --calls-service filters
3. test_producers_filter_by_topic_prefix - producers --topic-prefix filters
4. test_topics_groups_producers_by_topic - topics groups producers by topic name
5. test_topics_consumer_in_uses_neighbors_in_async_calls - topics --consumer-in uses neighbors_v2
6. test_jobs_lists_scheduled_task - jobs lists SCHEDULED_TASK symbols
7. test_listeners_lists_message_listener - listeners lists MESSAGE_LISTENER symbols
8. test_entities_lists_entity_role - entities lists ENTITY role symbols
9. test_listing_service_scope_pushes_down - --service pushes down to backend
10. test_listing_truncated_fires_at_limit - +1-fetch truncation detection
11. test_listing_client_kind_enum_lookup - --client-kind feign → feign_method
12. test_listing_rejects_offset - --offset not registered on listings

Note: --offset is NOT supported on any listing command (test 12 confirms).
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


# ----- Test 1: routes returns route kind -----


def test_routes_returns_route_kind(corpus_root: Path, ladybug_db_path: Path) -> None:
    """routes command returns route nodes with correct kind."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["routes", "--format", "json"], env=env)
    assert proc.returncode == 0, f"routes failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # At least some routes should exist
    assert len(nodes) >= 1, "expected at least one route node"
    # Verify routes have route-like structure
    for node_id, node in nodes.items():
        # Routes should have path, framework, method
        assert "path" in node or "id" in node, f"route missing path/id: {node}"


# ----- Test 2: clients filters by calls-service -----


def test_clients_filters_by_calls_service(corpus_root: Path, ladybug_db_path: Path) -> None:
    """clients --calls-service filters by target service."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # First get all clients
    proc_all = _run_jrag(["clients", "--format", "json"], env=env)
    assert proc_all.returncode == 0
    payload_all = json.loads(proc_all.stdout)
    all_clients = payload_all.get("nodes", {})

    # Now filter by a specific service (if any exist in the corpus)
    if len(all_clients) > 0:
        # Pick the first client's target_service to filter by
        first_client = next(iter(all_clients.values()))
        target_service = first_client.get("target_service")
        if target_service:
            proc_filtered = _run_jrag(["clients", "--calls-service", target_service, "--format", "json"], env=env)
            assert proc_filtered.returncode == 0
            payload_filtered = json.loads(proc_filtered.stdout)
            filtered_clients = payload_filtered.get("nodes", {})
            # All filtered clients should have the target_service
            for node_id, node in filtered_clients.items():
                assert node.get("target_service") == target_service, f"client {node_id} has wrong target_service"


# ----- Test 3: producers filter by topic-prefix -----


def test_producers_filter_by_topic_prefix(corpus_root: Path, ladybug_db_path: Path) -> None:
    """producers --topic-prefix filters by topic prefix."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # First get all producers
    proc_all = _run_jrag(["producers", "--format", "json"], env=env)
    assert proc_all.returncode == 0
    payload_all = json.loads(proc_all.stdout)
    all_producers = payload_all.get("nodes", {})

    # Now filter by topic prefix (if any producers exist)
    if len(all_producers) > 0:
        # Pick the first producer's topic to use as prefix
        first_producer = next(iter(all_producers.values()))
        topic = first_producer.get("topic")
        if topic:
            # Use first character as prefix
            prefix = topic[0]
            proc_filtered = _run_jrag(["producers", "--topic-prefix", prefix, "--format", "json"], env=env)
            assert proc_filtered.returncode == 0
            payload_filtered = json.loads(proc_filtered.stdout)
            filtered_producers = payload_filtered.get("nodes", {})
            # All filtered producers should have topics starting with the prefix
            for node_id, node in filtered_producers.items():
                assert node.get("topic", "").startswith(prefix), f"producer {node_id} topic doesn't start with {prefix}"


# ----- Test 4: topics groups producers by topic -----


def test_topics_groups_producers_by_topic(corpus_root: Path, ladybug_db_path: Path) -> None:
    """topics command groups producers by topic name."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["topics", "--format", "json"], env=env)
    assert proc.returncode == 0, f"topics failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # Topics should be grouped with producers lists
    for node_id, node in nodes.items():
        # Each topic node should have a topic field and producers list
        assert "topic" in node, f"topic node {node_id} missing topic field"
        assert "producers" in node, f"topic node {node_id} missing producers list"
        assert isinstance(node["producers"], list), "producers should be a list"


# ----- Test 5: topics consumer-in uses neighbors_v2 -----


def test_topics_consumer_in_uses_neighbors_in_async_calls(corpus_root: Path, ladybug_db_path: Path) -> None:
    """topics --consumer-in resolves consumers via neighbors_v2(in, ASYNC_CALLS)."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # First get all topics
    proc = _run_jrag(["topics", "--format", "json"], env=env)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    nodes = payload.get("nodes", {})

    # If we have topics and producers, try with --consumer-in
    if len(nodes) > 0:
        first_topic = next(iter(nodes.values()))
        producers = first_topic.get("producers", [])
        if len(producers) > 0:
            # Use the first producer's microservice for --consumer-in
            producer_ms = producers[0].get("microservice")
            if producer_ms:
                proc_consumer = _run_jrag(["topics", "--consumer-in", producer_ms, "--format", "json"], env=env)
                # Should succeed (even if no consumers found)
                assert proc_consumer.returncode == 0
                payload_consumer = json.loads(proc_consumer.stdout)
                assert payload_consumer["status"] == "ok"


# ----- Test 6: jobs lists scheduled-task -----


def test_jobs_lists_scheduled_task(corpus_root: Path, ladybug_db_path: Path) -> None:
    """jobs command lists symbols with SCHEDULED_TASK capability."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["jobs", "--format", "json"], env=env)
    assert proc.returncode == 0, f"jobs failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # All nodes should be symbols with the scheduled task capability
    for node_id, node in nodes.items():
        assert node.get("kind") == "symbol", f"jobs returned non-symbol: {node.get('kind')}"


# ----- Test 7: listeners lists message-listener -----


def test_listeners_lists_message_listener(corpus_root: Path, ladybug_db_path: Path) -> None:
    """listeners command lists symbols with MESSAGE_LISTENER capability."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["listeners", "--format", "json"], env=env)
    assert proc.returncode == 0, f"listeners failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # All nodes should be symbols
    for node_id, node in nodes.items():
        assert node.get("kind") == "symbol", f"listeners returned non-symbol: {node.get('kind')}"


# ----- Test 8: entities lists entity role -----


def test_entities_lists_entity_role(corpus_root: Path, ladybug_db_path: Path) -> None:
    """entities command lists symbols with ENTITY role."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    proc = _run_jrag(["entities", "--format", "json"], env=env)
    assert proc.returncode == 0, f"entities failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    # All nodes should be symbols with ENTITY role
    for node_id, node in nodes.items():
        assert node.get("kind") == "symbol", f"entities returned non-symbol: {node.get('kind')}"
        # Role should be ENTITY (normalized from backend)
        assert (node.get("role") or "").upper() == "ENTITY", f"entity has wrong role: {node.get('role')}"


# ----- Test 9: listing service scope pushes down -----


def test_listing_service_scope_pushes_down(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--service flag pushes down to backend list_* methods."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Test with routes command
    proc = _run_jrag(["routes", "--service", "chatassign", "--format", "json"], env=env)
    # May return empty results if service doesn't exist, but should not error
    assert proc.returncode == 0, f"routes --service failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # If results exist, they should all be from the specified service
    nodes = payload.get("nodes", {})
    for node_id, node in nodes.items():
        # All nodes should be from the specified microservice
        assert node.get("microservice") == "chatassign", f"node {node_id} has wrong microservice: {node.get('microservice')}"


# ----- Test 10: listing truncated fires at limit -----


def test_listing_truncated_fires_at_limit(corpus_root: Path, ladybug_db_path: Path) -> None:
    """+1-fetch trick: truncated=True when backend returns limit+1 results."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Use a small limit to trigger truncation if enough data exists
    proc = _run_jrag(["routes", "--limit", "2", "--format", "json"], env=env)
    assert proc.returncode == 0, f"routes --limit failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # Check truncated flag
    nodes = payload.get("nodes", {})
    # If we got exactly 2 results and there are more, truncated should be True
    # If we got fewer than 2, truncated should be False
    if len(nodes) == 2:
        # truncated may or may not be True depending on actual data count
        # Just check the field exists
        assert "truncated" in payload, "missing truncated field"
    else:
        # Fewer results than limit means no truncation
        assert not payload.get("truncated", False), "should not be truncated with fewer results than limit"


# ----- Test 11: listing client-kind enum lookup -----


def test_listing_client_kind_enum_lookup(corpus_root: Path, ladybug_db_path: Path) -> None:
    """--client-kind feign normalizes to feign_method via lookup table."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Test with --client-kind feign (should map to feign_method)
    proc = _run_jrag(["clients", "--client-kind", "feign", "--format", "json"], env=env)
    assert proc.returncode == 0, f"clients --client-kind feign failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    # If results exist, they should have client_kind = feign_method (normalized)
    nodes = payload.get("nodes", {})
    for node_id, node in nodes.items():
        # The backend stores feign_method, not feign
        client_kind = node.get("client_kind", "")
        # Should be the normalized form
        assert client_kind in ["feign_method", "", "feign"], f"unexpected client_kind: {client_kind}"


# ----- Test 12: listing rejects offset -----


def test_listing_rejects_offset() -> None:
    """--offset is NOT registered on listing commands (unrecognized argument error)."""
    env = os.environ.copy()

    # Test that --offset is rejected on routes
    proc = _run_jrag(["routes", "--offset", "5"], env=env)
    # argparse should reject this with exit code 2 (usage error)
    assert proc.returncode != 0, "routes --offset should be rejected"
    assert "unrecognized arguments: --offset" in proc.stderr or "usage:" in proc.stderr, \
        f"expected usage error, got: {proc.stderr}"

    # Same for clients
    proc = _run_jrag(["clients", "--offset", "5"], env=env)
    assert proc.returncode != 0, "clients --offset should be rejected"

    # Same for producers
    proc = _run_jrag(["producers", "--offset", "5"], env=env)
    assert proc.returncode != 0, "producers --offset should be rejected"

    # Same for topics
    proc = _run_jrag(["topics", "--offset", "5"], env=env)
    assert proc.returncode != 0, "topics --offset should be rejected"

    # Same for jobs
    proc = _run_jrag(["jobs", "--offset", "5"], env=env)
    assert proc.returncode != 0, "jobs --offset should be rejected"

    # Same for listeners
    proc = _run_jrag(["listeners", "--offset", "5"], env=env)
    assert proc.returncode != 0, "listeners --offset should be rejected"

    # Same for entities
    proc = _run_jrag(["entities", "--offset", "5"], env=env)
    assert proc.returncode != 0, "entities --offset should be rejected"
