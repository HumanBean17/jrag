"""Tests for `jrag` listing commands (PR-JRAG-2).

Tests:
1. test_routes_returns_route_kind - routes command returns route nodes
2. test_clients_filters_by_calls_service - clients --calls-service filters
3. test_producers_filter_by_topic_contains - producers --topic-contains filters
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
    # Each route carries its kind plus at least one identifying field. Resolved
    # HTTP routes carry `path`, Kafka topic routes (kind=kafka_topic) carry
    # `topic`, and unresolved/phantom HTTP endpoints may carry only `method` +
    # `file`. The prior `or "id"` fallback was an always-true tautology (every
    # node has an id) and masked a missing defining field.
    for node_id, node in nodes.items():
        assert "kind" in node, f"route {node_id} missing kind: {node}"
        assert any(k in node for k in ("path", "topic", "method", "file")), (
            f"route {node_id} has no identifying field: {node}"
        )


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


# ----- Test 3: producers filter by topic-contains -----


def test_producers_filter_by_topic_contains(corpus_root: Path, ladybug_db_path: Path) -> None:
    """producers --topic-contains filters by topic substring."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # First get all producers
    proc_all = _run_jrag(["producers", "--format", "json"], env=env)
    assert proc_all.returncode == 0
    payload_all = json.loads(proc_all.stdout)
    all_producers = payload_all.get("nodes", {})

    # Now filter by topic substring (if any producers exist)
    if len(all_producers) > 0:
        # Pick the first producer's topic to use as substring
        first_producer = next(iter(all_producers.values()))
        topic = first_producer.get("topic")
        if topic:
            # Use first character as substring
            needle = topic[0]
            proc_filtered = _run_jrag(["producers", "--topic-contains", needle, "--format", "json"], env=env)
            assert proc_filtered.returncode == 0
            payload_filtered = json.loads(proc_filtered.stdout)
            filtered_producers = payload_filtered.get("nodes", {})
            # All filtered producers should have topics containing the substring
            for node_id, node in filtered_producers.items():
                assert needle in node.get("topic", ""), f"producer {node_id} topic doesn't contain {needle}"


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


# ----- Test 5: topics --consumer-in resolves consumers via EXPOSES -----


def test_topics_consumer_in_resolves_consumers_via_exposes(ladybug_graph) -> None:
    """topics --consumer-in resolves listener consumers via EXPOSES on Route.

    The original PR-JRAG-2 implementation traversed ASYNC_CALLS inbound to
    Producer nodes, which is the wrong edge model (ASYNC_CALLS run
    Producer -> Route per java_ontology.py:415-416). This test exercises the
    corrected resolver directly.

    Fixture reality: no producer topic literal overlaps a listener topic
    literal (producers carry unresolved constants like 'ChatTopics.*' or
    resolved 'banking.chat.audit'; listeners carry different forms). So
    `topics --consumer-in` will not attach consumers to producer-grouped topics
    on THIS fixture — but the EXPOSES-based resolver does resolve a known
    listener for a known resolved topic. We assert the resolver returns that
    listener for the exact topic 'banking.chat.compliance.review' consumed by
    ComplianceReviewListener in microservice 'chat-core'.
    """
    from java_codebase_rag.jrag import _resolve_topic_consumers

    consumers = _resolve_topic_consumers(
        ladybug_graph,
        topic="banking.chat.compliance.review",
        microservice="chat-core",
        contains=False,
    )
    assert len(consumers) >= 1, (
        f"expected ComplianceReviewListener resolved for "
        f"'banking.chat.compliance.review' in 'chat-core'; got {consumers}"
    )
    found = any("ComplianceReviewListener" in c.get("fqn", "") for c in consumers)
    assert found, (
        f"ComplianceReviewListener not in resolver result; got {[c.get('fqn') for c in consumers]}"
    )

    # Substring match should also find it under 'banking.chat'.
    consumers_prefix = _resolve_topic_consumers(
        ladybug_graph,
        topic="banking.chat",
        contains=True,
    )
    assert any("ComplianceReviewListener" in c.get("fqn", "") for c in consumers_prefix), (
        f"ComplianceReviewListener not in substring resolver result; "
        f"got {[c.get('fqn') for c in consumers_prefix]}"
    )


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


# ----- Test 7a: listeners --topic-contains narrows (real filter) -----


def test_listeners_topic_contains_narrows(corpus_root: Path, ladybug_db_path: Path) -> None:
    """listeners --topic-contains filters via listener_method -EXPOSES-> Route(topic).

    The bank-chat fixture has 3 MESSAGE_LISTENER symbols:
      - ComplianceReviewListener   (topic=banking.chat.compliance.review)
      - ChatKafkaListener          (topic=ChatTopics.INCOMING — unresolved constant)
      - DistributionTriggerListener(topic=${assign.kafka.distribution-topic} — placeholder)
    Filtering by 'banking.chat' must narrow to the proper subset containing
    only ComplianceReviewListener, proving --topic-contains is a real filter
    (not the previous include-all stub).
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # All listeners (no filter)
    proc_all = _run_jrag(["listeners", "--format", "json"], env=env)
    assert proc_all.returncode == 0
    payload_all = json.loads(proc_all.stdout)
    all_nodes = payload_all.get("nodes", {})
    all_count = len(all_nodes)
    assert all_count >= 1, "expected at least one listener in fixture"

    # Filtered by 'banking.chat' — known resolved substring on this fixture
    proc_filtered = _run_jrag(["listeners", "--topic-contains", "banking.chat", "--format", "json"], env=env)
    assert proc_filtered.returncode == 0, (
        f"listeners --topic-contains failed: rc={proc_filtered.returncode}\n"
        f"stdout={proc_filtered.stdout}\nstderr={proc_filtered.stderr}"
    )
    payload_filtered = json.loads(proc_filtered.stdout)
    filtered_nodes = payload_filtered.get("nodes", {})

    # Proper subset: strictly fewer than the unfiltered set.
    assert len(filtered_nodes) < all_count, (
        f"--topic-contains did not narrow: all={all_count}, filtered={len(filtered_nodes)}"
    )

    # The known listener-topic pair on this fixture: ComplianceReviewListener
    # consumes 'banking.chat.compliance.review' (resolved topic literal).
    found_compliance = False
    for node_id, node in filtered_nodes.items():
        fqn = node.get("fqn", "")
        if "ComplianceReviewListener" in fqn:
            found_compliance = True
            break
    assert found_compliance, (
        f"ComplianceReviewListener not in filtered set; got: "
        f"{[n.get('fqn') for n in filtered_nodes.values()]}"
    )


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
    """+1-fetch trick: truncated=True when the corpus has more routes than `limit`.

    The prior assertion only checked the field exists when exactly 2 rows
    returned — it never verified ``truncated is True``. This learns the true
    route count first, then asserts truncation actually fires when limit < total
    (and that exactly `limit` rows are returned).
    """
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    # Learn the true route count (high limit -> no truncation expected).
    proc_all = _run_jrag(["routes", "--limit", "499", "--format", "json"], env=env)
    assert proc_all.returncode == 0, f"routes --limit 499 failed: {proc_all.stderr}"
    total = len(json.loads(proc_all.stdout).get("nodes", {}))

    proc = _run_jrag(["routes", "--limit", "2", "--format", "json"], env=env)
    assert proc.returncode == 0, f"routes --limit failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    nodes = payload.get("nodes", {})
    if total > 2:
        assert len(nodes) == 2, f"expected exactly 2 rows (limit), got {len(nodes)} of {total}"
        assert payload.get("truncated") is True, (
            f"expected truncated=True when total={total} > limit=2; payload={payload.get('truncated')}"
        )
    else:
        # Corpus has ≤2 routes total: no truncation; all returned.
        assert not payload.get("truncated", False), (
            f"should not be truncated with total={total} ≤ limit=2"
        )


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
    # Results must carry the NORMALIZED client_kind. The backend stores
    # `feign_method`, never the raw `feign` the user typed — the prior assertion
    # accepted the un-normalized "feign", which would mask a normalization
    # regression (the lookup table is the whole point of this test).
    nodes = payload.get("nodes", {})
    for node_id, node in nodes.items():
        client_kind = node.get("client_kind", "")
        assert client_kind in ("feign_method", ""), (
            f"unexpected/un-normalized client_kind: {client_kind!r} (raw 'feign' must normalize to 'feign_method')"
        )


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
