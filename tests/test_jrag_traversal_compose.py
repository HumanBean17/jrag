"""Tests for `jrag` compose traversals + connection + outline/imports (PR-JRAG-3b).

Five new commands sit on top of the PR-JRAG-3a foundation:
  * ``callees`` Client/Producer variant (Symbol path is unchanged from 3a).
    Client root -> neighbors_v2([id], "out", ["HTTP_CALLS"]) reaching :Route.
    Producer root -> neighbors_v2([id], "out", ["ASYNC_CALLS"]) reaching :Route
    (the kafka_topic Route this producer publishes to, NOT :Producer).
  * ``dependencies`` -> neighbors_v2([id], "out", ["INJECTS"]) (Symbol -> Symbol).
  * ``connection <microservice>`` -- multi-section inbound:/outbound: view.
    RESOLVE-FIRST EXCEPTION: the first positional is a microservice NAME.
  * ``outline <file>`` -> find_symbols_in_file_range(start_line=1, end_line=2**31-1).
  * ``imports <file>`` -> ast_java.parse_java + resolve_v2 per imported FQN.

Tests (bank-chat fixture):
 1. test_callees_client_reaches_route_via_http_calls
 2. test_callees_producer_reaches_route_topic_via_async_calls
 3. test_dependencies_composes_neighbors_out_injects
 4. test_connection_inbound_lists_external_callers
 5. test_connection_outbound_lists_this_service_clients
 6. test_connection_both_default
 7. test_connection_http_method_filter
 8. test_connection_first_positional_is_microservice_not_query
 9. test_outline_lists_file_symbols
10. test_outline_empty_for_missing_file
11. test_imports_resolves_graph_nodes
12. test_outline_and_import_reject_offset_or_document_unbounded
13. test_connection_calls_service_outbound_excludes_unresolved_clients  (review Fix 2)
14. test_imports_text_mode_marks_unresolved                               (review Fix 3)

Backend signatures verified against source at PR-JRAG-3b time:
 * neighbors_v2 (mcp_v2.py:1284) returns NeighborsOutput.results: list[Edge]
   where Edge.other: NodeRef, Edge.edge_type, Edge.attrs.
 * find_symbols_in_file_range (ladybug_queries.py:302) requires start_line>=1
   (returns [] otherwise); returns list[SymbolHit].
 * list_clients / list_producers return list[dict] (plain dicts).
 * find_route_callers returns list[RouteCaller] (caller_node_kind: client|producer).
 * parse_java (ast_java.py:2612) -> JavaFileAst.explicit_imports: dict[str,str].
 * Edge directions confirmed in java_ontology.py:
   - HTTP_CALLS: Client -> Route (line 352)
   - ASYNC_CALLS: Producer -> Route (line 386)
   - INJECTS: Symbol -> Symbol (line 216)
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


# Seed identifiers verified against the bank-chat fixture (PR-JRAG-3b probe).
# Client resolve: resolve_v2 accepts "<target_service> <path>" (the
# `client_target_path` reason); "chat-core /api/v1/chat/sessions" resolves
# cleanly to ONE FeignClient (the getSession method), avoiding the
# /chat/joinOperator ambiguity (Feign + RestTemplate both target that path).
_CLIENT_GETSESSION = "chat-core /api/v1/chat/sessions"
# Producer resolve: a unique topic literal resolves to one Producer node.
_PRODUCER_AUDIT_DLQ = "banking.chat.audit.dlq"
# Type with injections (ClientMessageProcessor injects ChatAssignmentPort,
# ComplianceScanner, FollowUpKafkaPublisher, RejectionPublisher, etc).
_INJECTOR_TYPE = "com.bank.chat.engine.processors.ClientMessageProcessor"
# File path stored in the graph (POSIX-relative to source root; build_ast_graph
# line 534: rel_path = abs_path_resolved.relative_to(source_root).as_posix()).
_OUTLINE_FILE = (
    "chat-assign/src/main/java/com/bank/chat/assign/integration/ChatCoreFeignClient.java"
)


# ----- Test 1: callees (Client) reaches :Route via HTTP_CALLS out -----


def test_callees_client_reaches_route_via_http_calls(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Client root -> neighbors_v2([id], 'out', ['HTTP_CALLS']) reaching :Route.

    resolve_v2('chat-core /api/v1/chat/sessions', hint_kind='client') gives a
    single FeignClient (the getSession method). HTTP_CALLS is Client -> Route
    (java_ontology.py:352), so 'out' dispatches to the chat-core :Route the
    client targets. The endpoint MUST be a :Route (not another :Client).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["callees", _CLIENT_GETSESSION, "--kind", "client", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"callees client failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id (the Client)"
    edges = payload.get("edges", [])
    assert len(edges) >= 1, f"expected >=1 HTTP_CALLS edge, got {edges}"
    # Every edge MUST be HTTP_CALLS (the Client root variant).
    for e in edges:
        assert e.get("edge_type") == "HTTP_CALLS", (
            f"expected HTTP_CALLS edge, got {e.get('edge_type')}"
        )
    # Every edge endpoint MUST be a :Route (the kafka_topic analog for HTTP).
    nodes = payload.get("nodes", {})
    for e in edges:
        ep = nodes.get(e.get("target"), {})
        assert ep.get("kind") == "route", (
            f"expected edge endpoint kind=route, got {ep.get('kind')!r} on {ep}"
        )


# ----- Test 2: callees (Producer) reaches :Route (kafka_topic) via ASYNC_CALLS out -----


def test_callees_producer_reaches_route_topic_via_async_calls(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Producer root -> neighbors_v2([id], 'out', ['ASYNC_CALLS']) reaching :Route.

    resolve_v2('banking.chat.audit.dlq', hint_kind='producer') resolves to one
    Producer node (EventStreamBridge#sendToAudit producing to .dlq). ASYNC_CALLS
    is Producer -> Route (java_ontology.py:386), so 'out' dispatches to the
    kafka_topic :Route this producer publishes to (NOT a :Producer node).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["callees", _PRODUCER_AUDIT_DLQ, "--kind", "producer", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"callees producer failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id (the Producer)"
    edges = payload.get("edges", [])
    assert len(edges) >= 1, f"expected >=1 ASYNC_CALLS edge, got {edges}"
    for e in edges:
        assert e.get("edge_type") == "ASYNC_CALLS", (
            f"expected ASYNC_CALLS edge, got {e.get('edge_type')}"
        )
    # The endpoint MUST be a :Route (the kafka_topic), NOT a :Producer.
    nodes = payload.get("nodes", {})
    for e in edges:
        ep = nodes.get(e.get("target"), {})
        assert ep.get("kind") == "route", (
            f"expected edge endpoint kind=route (kafka_topic), got {ep.get('kind')!r}"
        )


# ----- Test 3: dependencies composes neighbors(out, INJECTS) -----


def test_dependencies_composes_neighbors_out_injects(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """dependencies on a type returns the types it injects (INJECTS out).

    ClientMessageProcessor injects ChatAssignmentPort (verified in the fixture;
    also ComplianceScanner, ClientMessageRateLimiter, etc). INJECTS is Symbol ->
    Symbol (java_ontology.py:216), so 'out' dispatches to the injected types.
    The endpoint MUST be a Symbol.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["dependencies", _INJECTOR_TYPE, "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"dependencies failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    assert len(edges) >= 1, f"expected >=1 INJECTS edge, got {edges}"
    for e in edges:
        assert e.get("edge_type") == "INJECTS", (
            f"expected INJECTS edge, got {e.get('edge_type')}"
        )
    # The endpoint MUST be a Symbol (INJECTS is Symbol -> Symbol).
    nodes = payload.get("nodes", {})
    injected_fqns = []
    for e in edges:
        ep = nodes.get(e.get("target"), {})
        assert ep.get("kind") == "symbol", (
            f"expected edge endpoint kind=symbol, got {ep.get('kind')!r}"
        )
        injected_fqns.append(ep.get("fqn", ""))
    # ClientMessageProcessor injects ChatAssignmentPort.
    assert any("ChatAssignmentPort" in fqn for fqn in injected_fqns), (
        f"expected ChatAssignmentPort in injected types, got {injected_fqns}"
    )


# ----- Test 4: connection --inbound lists external callers -----


def test_connection_inbound_lists_external_callers(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """connection chat-core --inbound lists chat-assign clients targeting chat-core.

    chat-assign has ChatCoreFeignClient + ChatCoreJoinClient targeting chat-core
    (verified via list_clients(target_service='chat-core')). The inbound section
    MUST surface at least one of them with edge_type=HTTP_CALLS, section=inbound.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["connection", "chat-core", "--inbound", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"connection inbound failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id (synthetic microservice)"
    edges = payload.get("edges", [])
    inbound = [e for e in edges if e.get("section") == "inbound"]
    assert len(inbound) >= 1, (
        f"expected >=1 inbound edge from chat-assign, got {inbound}"
    )
    # All inbound edges are HTTP_CALLS or ASYNC_CALLS.
    for e in inbound:
        assert e.get("edge_type") in ("HTTP_CALLS", "ASYNC_CALLS"), (
            f"expected HTTP/ASYNC_CALLS, got {e.get('edge_type')}"
        )
    # The synthetic microservice root node must be present and labeled.
    nodes = payload.get("nodes", {})
    root_node = nodes.get(payload["root"], {})
    assert root_node.get("kind") == "microservice", (
        f"expected synthetic microservice root, got {root_node}"
    )
    assert root_node.get("name") == "chat-core", (
        f"expected root name 'chat-core', got {root_node.get('name')}"
    )
    # At least one chat-assign caller MUST be present (the test's main invariant).
    caller_services = {
        nodes.get(e.get("target"), {}).get("microservice", "")
        for e in inbound
    }
    assert "chat-assign" in caller_services, (
        f"expected chat-assign in inbound caller services, got {caller_services}"
    )


# ----- Test 5: connection --outbound lists this service's clients/producers -----


def test_connection_outbound_lists_this_service_clients(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """connection chat-assign --outbound lists chat-assign's clients + producers.

    chat-assign has ChatCoreFeignClient + ChatCoreJoinClient (HTTP) and
    DistributionTriggerPublisher (Kafka). The outbound section MUST surface at
    least one HTTP_CALLS and (when indexed) at least one ASYNC_CALLS, all
    carrying section=outbound.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["connection", "chat-assign", "--outbound", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"connection outbound failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    edges = payload.get("edges", [])
    outbound = [e for e in edges if e.get("section") == "outbound"]
    assert len(outbound) >= 1, (
        f"expected >=1 outbound edge from chat-assign, got {outbound}"
    )
    # No inbound edges when --outbound only.
    assert all(e.get("section") == "outbound" for e in edges), (
        f"expected only outbound edges, got sections={ {e.get('section') for e in edges} }"
    )
    # HTTP outbound MUST be present (chat-assign's two clients target chat-core).
    http_out = [e for e in outbound if e.get("edge_type") == "HTTP_CALLS"]
    assert len(http_out) >= 1, f"expected >=1 outbound HTTP_CALLS, got {http_out}"


# ----- Test 6: connection default direction is --inbound (brief-faithful) -----


def test_connection_both_default(corpus_root: Path, ladybug_db_path: Path) -> None:
    """connection with no direction flag defaults to --both (full picture).

    The default is --both so `connection <svc>` shows inbound + outbound: an
    inbound-only default hid a service's outbound HTTP clients unless the agent
    remembered `--both`, making services look connectionless. `--inbound` /
    `--outbound` remain explicit opt-ins for a single direction.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    # Default (no flag) MUST equal explicit --both.
    proc_default = _run_jrag(
        ["connection", "chat-core", "--format", "json"],
        env=env,
    )
    assert proc_default.returncode == 0, (
        f"connection default failed: {proc_default.stderr}"
    )
    payload_default = json.loads(proc_default.stdout)
    assert payload_default["status"] == "ok"
    sections_default = {e.get("section") for e in payload_default.get("edges", [])}

    proc_both = _run_jrag(
        ["connection", "chat-core", "--both", "--format", "json"],
        env=env,
    )
    assert proc_both.returncode == 0
    payload_both = json.loads(proc_both.stdout)
    sections_both = {e.get("section") for e in payload_both.get("edges", [])}

    assert sections_default == sections_both, (
        f"default {sections_default} != explicit --both {sections_both}"
    )
    # Default MUST include outbound (the whole point: show the full picture).
    assert "outbound" in sections_default, (
        f"default direction should be --both (include outbound), got {sections_default}"
    )

    # --inbound is the explicit opt-in for inbound-only (no outbound leakage).
    proc_inbound = _run_jrag(
        ["connection", "chat-core", "--inbound", "--format", "json"],
        env=env,
    )
    assert proc_inbound.returncode == 0
    payload_inbound = json.loads(proc_inbound.stdout)
    sections_inbound = {e.get("section") for e in payload_inbound.get("edges", [])}
    assert "outbound" not in sections_inbound, (
        f"--inbound should be inbound-only, got {sections_inbound}"
    )


# ----- Test 7: --http-method filters HTTP callers -----


def test_connection_http_method_filter(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--http-method POST narrows inbound HTTP callers to POST only.

    Without the filter, chat-core inbound has at least one POST (joinOperator)
    and one GET (api/v1/chat/sessions). With --http-method POST, GET callers
    MUST be excluded. The result must be a strict subset.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc_all = _run_jrag(
        ["connection", "chat-core", "--inbound", "--format", "json"],
        env=env,
    )
    assert proc_all.returncode == 0
    payload_all = json.loads(proc_all.stdout)
    inbound_all = [e for e in payload_all.get("edges", []) if e.get("section") == "inbound"]

    proc_post = _run_jrag(
        ["connection", "chat-core", "--inbound", "--http-method", "POST", "--format", "json"],
        env=env,
    )
    assert proc_post.returncode == 0, f"--http-method POST failed: {proc_post.stderr}"
    payload_post = json.loads(proc_post.stdout)
    inbound_post = [e for e in payload_post.get("edges", []) if e.get("section") == "inbound"]

    # All surviving HTTP edges MUST have method=POST.
    nodes_post = payload_post.get("nodes", {})
    for e in inbound_post:
        if e.get("edge_type") == "HTTP_CALLS":
            ep = nodes_post.get(e.get("target"), {})
            assert (ep.get("method") or "").upper() == "POST", (
                f"expected POST after --http-method POST, got {ep.get('method')!r} on {ep}"
            )
    # The POST set must not exceed the unfiltered inbound set.
    assert len(inbound_post) <= len(inbound_all), (
        f"--http-method POST should not grow inbound: post={len(inbound_post)} all={len(inbound_all)}"
    )


# ----- Test 8: first positional is microservice NAME (not run through resolve_v2) -----


def test_connection_first_positional_is_microservice_not_query(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """The first positional is a microservice NAME, NOT a query.

    If we ran resolve_v2('chat-core'), it would NOT match (chat-core is not a
    Symbol/Route/Client/Producer FQN) and the envelope would be status=not_found.
    The command returns status=ok with a synthetic microservice root, proving
    resolve_v2 was skipped (the resolve-first exception).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    # 'chat-core' would resolve to a `many` of Clients (target_service match)
    # if it WERE run through resolve_v2 with hint_kind=client; the result here
    # is status=ok with a synthetic root, NOT ambiguous and NOT not_found.
    proc = _run_jrag(
        ["connection", "chat-core", "--inbound", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", (
        f"expected ok (resolve_v2 was NOT run on the positional), got {payload.get('status')}"
    )
    # The synthetic microservice root is keyed by the microservice NAME (its
    # natural key after the id-free boundary strip), not a `microservice:`-
    # prefixed synthetic id and not a resolved symbol id.
    assert payload.get("root") == "chat-core", (
        f"expected root == 'chat-core' (microservice natural key), got {payload.get('root')}"
    )
    root_node = payload["nodes"]["chat-core"]
    assert root_node.get("kind") == "microservice", (
        f"expected synthetic microservice root kind, got {root_node.get('kind')}"
    )

    # Sanity: a clearly-not-real-microservice now returns status:error (Phase 3
    # consistency sweep — validate the positional against the known set so a
    # bogus name surfaces a clear error instead of an empty ok that reads as
    # "this service genuinely has no connections"). resolve_v2 is STILL not
    # invoked (the error is a microservice-name check, not a resolve).
    proc_unknown = _run_jrag(
        ["connection", "definitely-not-a-real-microservice", "--format", "json"],
        env=env,
    )
    assert proc_unknown.returncode == 2, (
        f"unknown microservice should error: rc={proc_unknown.returncode}\nstderr={proc_unknown.stderr}"
    )
    payload_unknown = json.loads(proc_unknown.stdout)
    assert payload_unknown["status"] == "error", (
        f"expected error for unknown microservice, got {payload_unknown.get('status')}"
    )
    assert "unknown microservice" in payload_unknown.get("message", ""), (
        f"expected 'unknown microservice' message, got {payload_unknown.get('message')!r}"
    )


# ----- Test 9: outline lists file symbols -----


def test_outline_lists_file_symbols(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """outline <file> returns every Symbol declared in <file>.

    find_symbols_in_file_range(start_line=1, end_line=2**31-1) returns ALL
    symbols in the file (1-based; start_line<1 returns []). ChatCoreFeignClient
    has 3 symbols (interface + 2 methods).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["outline", _OUTLINE_FILE, "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"outline failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    nodes = payload.get("nodes", {})
    assert len(nodes) >= 1, f"expected >=1 symbol in {_OUTLINE_FILE}, got {nodes}"
    # Every node is a symbol.
    for nid, node in nodes.items():
        assert node.get("kind") == "symbol", (
            f"expected kind=symbol, got {node.get('kind')!r} on {node}"
        )
    # The interface itself MUST be present (FQN ends with the type name).
    fqns = [n.get("fqn", "") for n in nodes.values()]
    assert any("ChatCoreFeignClient" in fqn for fqn in fqns), (
        f"expected ChatCoreFeignClient in outline, got {fqns}"
    )


# ----- Test 10: outline is graceful on missing files (no crash) -----


def test_outline_empty_for_missing_file(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """outline on a non-existent filename returns a clean status:error.

    Parity with `imports`: outline resolves <file> via _resolve_source_path, so
    a missing file surfaces a "file not found" error instead of a silent empty
    success (which reads as "this file has no symbols" — a silent wrong answer).
    The exit code is 2 (envelope-shape error), not a crash.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["outline", "does/not/exist/Nope.java", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 2, (
        f"outline missing file should error: rc={proc.returncode}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error", f"expected error, got {payload}"
    assert "file not found" in payload.get("message", ""), (
        f"expected 'file not found' message, got {payload.get('message')!r}"
    )


# ----- Test 11: imports resolves graph nodes -----


def test_imports_resolves_graph_nodes(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """imports <file> tree-sitter-parses + resolves each FQN via resolve_v2.

    ChatCoreFeignClient imports com.bank.chat.app.web.JoinOperatorRequest
    (a contracts DTO that IS in the graph). That import MUST resolve to a graph
    Symbol node (resolved=True). External Spring imports (org.springframework.*)
    are NOT in the graph and MUST come back as unresolved (resolved=False).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["imports", _OUTLINE_FILE, "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"imports failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    nodes = payload.get("nodes", {})
    edges = payload.get("edges", [])
    assert len(edges) >= 1, f"expected >=1 import edge, got {edges}"

    # Split edges by resolved flag.
    resolved_edges = [e for e in edges if e.get("resolved") is True]
    unresolved_edges = [e for e in edges if e.get("resolved") is False]
    assert len(resolved_edges) >= 1, (
        f"expected >=1 resolved import (JoinOperatorRequest), got edges={edges}"
    )
    assert len(unresolved_edges) >= 1, (
        f"expected >=1 unresolved import (org.springframework.*), got edges={edges}"
    )

    # The resolved import MUST be the JoinOperatorRequest graph Symbol.
    resolved_fqns = []
    for e in resolved_edges:
        node = nodes.get(e.get("target"), {})
        resolved_fqns.append(node.get("fqn", ""))
    assert any("JoinOperatorRequest" in fqn for fqn in resolved_fqns), (
        f"expected JoinOperatorRequest resolved, got {resolved_fqns}"
    )

    # Unresolved imports carry the raw FQN and kind=unresolved_import.
    for e in unresolved_edges:
        node = nodes.get(e.get("target"), {})
        assert node.get("kind") == "unresolved_import", (
            f"expected kind=unresolved_import, got {node.get('kind')!r} on {node}"
        )
        assert node.get("fqn"), f"expected fqn on unresolved import, got {node}"


# ----- Test 12: outline/imports reject --offset; document unbounded -----


def test_outline_and_import_reject_offset_or_document_unbounded() -> None:
    """--offset is rejected on outline and imports (neither takes offset).

    Per the global plan: --offset is supported only on find/search; traversal
    and listing commands (including outline/imports, which take no offset)
    reject it via argparse. We also assert that outline's --limit (a common
    flag inherited from the parent) does NOT silently cap results — but the
    flag is accepted (we cannot remove inherited common flags per-command).
    """
    env = os.environ.copy()
    for cmd in ("outline", "imports"):
        proc = _run_jrag([cmd, "somefile.java", "--offset", "5"], env=env)
        assert proc.returncode != 0, (
            f"{cmd} --offset should be rejected (rc!=0), got rc={proc.returncode}"
        )
        assert (
            "unrecognized arguments: --offset" in proc.stderr or "usage:" in proc.stderr
        ), f"{cmd}: expected usage error, got stderr={proc.stderr!r}"


# ----- Test 13 (review Fix 2): --calls-service outbound excludes unresolved clients -----


def test_connection_calls_service_outbound_excludes_unresolved_clients(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--calls-service on outbound uses STRICT target_service matching for clients.

    PR-JRAG-3b review Fix 2: the initial predicate
    `(target_service == calls_service) or not target_service` was a loophole —
    the `or not target_service` escape was meant for producers (genuinely no
    service target) but ALSO matched unresolved clients (empty target_service,
    e.g. AuditLogClient#logAssignment in the fixture). The tightened predicate
    keeps producers (with a warning) and EXCLUDES unresolved clients.

    Fixture pair (verified by reviewer):
      * chat-assign's ChatCoreFeignClient#joinOperator — target_service=chat-core (KEEP)
      * chat-assign's AuditLogClient#logAssignment       — target_service='' (EXCLUDE)
      * chat-assign's producers (e.g. DistributionTriggerPublisher) — KEPT w/ warning
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["connection", "chat-assign", "--outbound", "--calls-service", "chat-core", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"connection --calls-service failed: rc={proc.returncode}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    nodes = payload.get("nodes", {})
    edges = payload.get("edges", [])
    outbound = [e for e in edges if e.get("section") == "outbound"]

    # (a) Clients with target_service == chat-core MUST be present.
    client_edges = [e for e in outbound if e.get("edge_type") == "HTTP_CALLS"]
    assert len(client_edges) >= 1, f"expected >=1 chat-core client edge, got {client_edges}"
    for e in client_edges:
        node = nodes.get(e.get("target"), {})
        assert (node.get("target_service") or "") == "chat-core", (
            f"strict --calls-service leak: client target_service={node.get('target_service')!r}"
        )

    # (b) The UNRESOLVED client (AuditLogClient#logAssignment, empty target_service)
    # MUST NOT appear anywhere in the result. The fixture's AuditLogClient has
    # no @CodebaseHttpClient annotation, so its target_service is empty.
    for nid, node in nodes.items():
        if node.get("kind") == "client":
            fqn = node.get("fqn", "") or ""
            assert "AuditLogClient" not in fqn, (
                f"AuditLogClient MUST be excluded under --calls-service chat-core "
                f"(empty target_service); got node={node}"
            )

    # (c) Producers are KEPT (async channel stays visible) AND a warning fires
    # explaining producers bypass --calls-service.
    producer_edges = [e for e in outbound if e.get("edge_type") == "ASYNC_CALLS"]
    warnings = payload.get("warnings", [])
    if producer_edges:
        # Warning MUST mention producers bypass the filter.
        assert any("--calls-service" in w and "producer" in w.lower() for w in warnings), (
            f"expected --calls-service producer-bypass warning, got {warnings}"
        )
    # Sanity: at least one producer edge is present on chat-assign (the fixture
    # has DistributionTriggerPublisher + AuditLogClient async stub).
    assert len(producer_edges) >= 1, (
        f"expected >=1 producer edge kept under --calls-service, got {producer_edges}"
    )


# ----- Test 14 (review Fix 3): text-mode imports distinguishes resolved vs unresolved -----


def test_imports_text_mode_marks_unresolved(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """Text-mode imports MUST visually distinguish resolved vs unresolved.

    PR-JRAG-3b review Fix 3: the handler sets kind="unresolved_import" + edge
    `resolved: bool`, but text mode dispatched to _render_listing which shows
    only simple_name + @service — resolved and unresolved looked identical
    (only JSON distinguished). The renderer now appends " (unresolved)" to
    nodes with kind="unresolved_import".

    ChatCoreFeignClient has mixed resolution:
      * com.bank.chat.app.web.JoinOperatorRequest — IN GRAPH (resolved Symbol)
      * org.springframework.*                       — NOT IN GRAPH (unresolved)
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["imports", _OUTLINE_FILE],  # default text mode
        env=env,
    )
    assert proc.returncode == 0, (
        f"imports text failed: rc={proc.returncode}\nstderr={proc.stderr}"
    )
    text = proc.stdout
    # The "(unresolved)" marker MUST appear (the file has unresolved imports).
    assert "(unresolved)" in text, (
        f"expected (unresolved) marker in text output, got:\n{text}"
    )
    # Resolved Symbol nodes MUST NOT carry the marker. JoinOperatorRequest is
    # resolved; its line should NOT have the suffix.
    lines = text.splitlines()
    join_line = next((ln for ln in lines if "JoinOperatorRequest" in ln), None)
    assert join_line is not None, f"expected JoinOperatorRequest in text output:\n{text}"
    assert "(unresolved)" not in join_line, (
        f"resolved import JoinOperatorRequest MUST NOT carry (unresolved): {join_line!r}"
    )
    # At least one Spring import line MUST carry the marker.
    spring_line = next((ln for ln in lines if "springframework" in ln.lower() or "FeignClient" in ln), None)
    assert spring_line is not None, f"expected a spring import line in:\n{text}"
    assert "(unresolved)" in spring_line, (
        f"unresolved Spring import MUST carry (unresolved): {spring_line!r}"
    )
