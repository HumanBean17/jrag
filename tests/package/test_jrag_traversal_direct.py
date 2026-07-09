"""Tests for `jrag` direct-backend traversal commands (PR-JRAG-3a).

The 11 traversal subcommands: callers, callees, hierarchy, implementations,
subclasses, overrides, overridden-by, dependents, impact, decompose, flow.
Each is resolve-first then calls a LadybugGraph method (or neighbors_v2 for
the override axis), then renders via the traversal shape (root + edge rows).
``--offset`` is NOT supported on any traversal.

Tests (bank-chat fixture):
1.  test_callers_symbol_uses_find_callers
2.  test_callers_route_service_is_post_filter_with_warning
3.  test_callees_symbol_uses_find_callees
4.  test_callers_and_callees_support_include_external
5.  test_hierarchy_renders_tree_both_directions
6.  test_implementations_uses_find_implementors
7.  test_implementations_capability_post_filter
8.  test_subclasses_uses_find_subclasses
9.  test_overrides_dispatches_up_via_neighbors_out_overrides
10. test_overridden_by_dispatches_down_via_neighbors_in_overrides
11. test_dependents_uses_find_injectors
12. test_impact_runs_fleet_wide_without_service
13. test_impact_service_post_filter_emits_warning
14. test_decompose_renders_role_waterfall
15. test_flow_outbound_intra_service_on_fixture
16. test_flow_follows_kafka_topic_on_fixture
17. test_flow_depth_flag_and_max_hops_alias
18. test_callers_topic_disambiguates_with_kind
19. test_traversal_resolve_ambiguous_stops
20. test_traversal_rejects_offset
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
        encoding="utf-8",  # jrag emits UTF-8 (↑/↓ tree headers); decode as such, not the locale ANSI codepage (cp1252 on Windows).
        env=env,
        input=stdin,
        check=False,
    )


def _env_for(corpus_root: Path, ladybug_db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)
    return env


# Seed nodes verified against the bank-chat fixture (PR-JRAG-3a probe).
# Method FQNs MUST include parameter types in parens for resolve_v2 to match.
_SVC_ASSIGN = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
_PORT_METHOD = "com.bank.chat.engine.assign.ChatAssignmentPort#requestAssignment(AssignmentRequest)"
_IMPL_METHOD = "com.bank.chat.engine.assign.ConfigurableChatAssignment#requestAssignment(AssignmentRequest)"
_PORT_TYPE = "com.bank.chat.engine.assign.ChatAssignmentPort"
_ABS_NOTIFICATION = "com.bank.chat.engine.notification.AbstractNotificationSender"
_INGRESS_CTRL = "com.bank.chat.app.web.ChatIngressController"


# ----- Test 1: callers (Symbol) uses find_callers -----


def test_callers_symbol_uses_find_callers(corpus_root: Path, ladybug_db_path: Path) -> None:
    """callers on a Symbol calls find_callers and returns the caller as an edge."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["callers", _SVC_ASSIGN, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"callers failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    # Root must be set (traversal shape).
    assert payload.get("root"), "expected root id set on the envelope"
    # The controller method that calls ChatManagementService#assign must appear.
    edges = payload.get("edges", [])
    assert len(edges) >= 1, f"expected at least one caller edge, got {edges}"
    nodes = payload.get("nodes", {})
    # At least one edge endpoint should be the ChatManagementController#assign caller.
    caller_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    assert any("ChatManagementController#assign" in fqn for fqn in caller_fqns), (
        f"ChatManagementController#assign not in caller fqns {caller_fqns}"
    )


# ----- Test 1b: callers on a controller CLASS surfaces its EXPOSES routes -----


def test_callers_on_controller_class_surfaces_exposes_routes(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """callers on a controller type folds in the routes its methods EXPOSE.

    A controller is an HTTP entry point: its handler methods are invoked via
    EXPOSES (the framework dispatches the route), not via in-repo CALLS edges.
    So `callers <Controller>` must surface those routes as EXPOSES rows rather
    than returning a bug-looking empty CALLS-in list. The routes are additive
    to any CALLS-in edges (the consumer contract: "not only CALLS").
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["callers", _INGRESS_CTRL, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"callers failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    expose_edges = [e for e in edges if e.get("edge_type") == "EXPOSES"]
    assert expose_edges, (
        f"expected at least one EXPOSES edge for a controller root, got edge types "
        f"{[e.get('edge_type') for e in edges]}"
    )
    # Every EXPOSES target must be a route node carrying an HTTP method+path.
    for e in expose_edges:
        tgt = nodes.get(e.get("target"), {})
        assert tgt.get("kind") == "route", f"EXPOSES target is not a route: {tgt}"
        assert tgt.get("method"), f"EXPOSES route missing method: {tgt}"
        assert tgt.get("path"), f"EXPOSES route missing path: {tgt}"
    # Text rendering: the route must appear under the root (not a bare "0 callers").
    proc_text = _run_jrag(["callers", _INGRESS_CTRL], env=env)
    assert proc_text.returncode == 0, f"text callers failed: {proc_text.stderr}"
    assert "/api/v1/chat/events" in proc_text.stdout, (
        f"expected the exposed route path in text output:\n{proc_text.stdout}"
    )


# ----- Test 2: callers (Route) --service narrows resolve (no post-filter) -----


def test_callers_route_service_narrows_resolve_no_post_filter(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """callers on a Route with --service narrows resolve; no post-filter warning.

    Phase 1 changed --service to a resolve-time filter (it pushes down into
    resolve_query so `callers '/path' --service <ms>` selects that
    microservice's route). Route callers are cross-service by construction, so
    --service is NOT applied as a caller-microservice post-filter and no
    post-filter warning fires. Verified on /chat/joinOperator: --service
    chat-core resolves the chat-core route and still returns the chat-assign
    cross-service callers.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["callers", "/chat/joinOperator", "--service", "chat-core", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, (
        f"callers route failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id (the Route)"
    # The cross-service callers from chat-assign MUST survive --service chat-core
    # (they are NOT filtered out by --service on the route-caller path).
    edges = payload.get("edges", [])
    assert len(edges) >= 1, f"expected cross-service route callers, got edges={edges}"
    # No post-filter warning: --service now narrows resolve, not caller results.
    warnings = payload.get("warnings", [])
    assert not any("post-filter" in w for w in warnings), (
        f"--service should not emit a post-filter warning, got warnings={warnings}"
    )


# ----- Test 3: callees (Symbol) uses find_callees -----


def test_callees_symbol_uses_find_callees(corpus_root: Path, ladybug_db_path: Path) -> None:
    """callees on a Symbol calls find_callees and returns callee edges."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["callees", _SVC_ASSIGN, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"callees failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id set"
    edges = payload.get("edges", [])
    # ChatManagementService#assign has ~20 callees in the fixture.
    assert len(edges) >= 1, f"expected at least one callee edge, got {edges}"
    # Each edge should carry edge_type=CALLS and a confidence.
    for e in edges:
        assert e.get("edge_type") == "CALLS", f"expected CALLS edge, got {e.get('edge_type')}"


# ----- Test 4: callers/callees support --include-external (symmetric) -----


def test_callers_and_callees_support_include_external(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--include-external is wired symmetrically on callers and callees.

    The flag maps to exclude_external = not --include-external on both
    sides. We verify the command ACCEPTS the flag and returns ok (the
    fixture's external-callee counts are not asserted here; the wiring is
    what matters — exclude_external=True is the default and the flag flips
    it). Verified via the help text and a clean rc=0 run on both commands.
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # callees WITH --include-external: should include JDK/Spring callees
    # (ChatManagementService#assign calls e.g. AssignQueueEntity setters, plus
    # possibly external types when not excluded).
    proc_in = _run_jrag(["callees", _SVC_ASSIGN, "--include-external", "--format", "json"], env=env)
    assert proc_in.returncode == 0, f"--include-external failed: {proc_in.stderr}"
    payload_in = json.loads(proc_in.stdout)
    assert payload_in["status"] == "ok"

    # callees WITHOUT --include-external (default: exclude).
    proc_out = _run_jrag(["callees", _SVC_ASSIGN, "--format", "json"], env=env)
    assert proc_out.returncode == 0
    payload_out = json.loads(proc_out.stdout)
    assert payload_out["status"] == "ok"

    # With --include-external the result set should be >= the excluded set
    # (external callees can only ADD to the result, never remove).
    edges_in = len(payload_in.get("edges", []))
    edges_out = len(payload_out.get("edges", []))
    assert edges_in >= edges_out, (
        f"--include-external should not shrink results: in={edges_in} out={edges_out}"
    )

    # callers accepts the flag too (symmetric wiring).
    proc_callers = _run_jrag(["callers", _SVC_ASSIGN, "--include-external", "--format", "json"], env=env)
    assert proc_callers.returncode == 0, f"callers --include-external failed: {proc_callers.stderr}"


# ----- Test 5: hierarchy renders both directions -----


def test_hierarchy_renders_tree_both_directions(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """hierarchy walks EXTENDS/IMPLEMENTS both directions AND renders a tree.

    AbstractNotificationSender: UP = NotificationSender (implements),
    DOWN = EmailNotificationSender + PushNotificationSender (extends).

    Asserts BOTH the data (JSON: up/down edge presence) AND the rendered
    structure (text: the ↑ supertypes / ↓ subtypes group headers that
    `_render_traversal` emits for direction-carrying edges). The text
    assertion is non-vacuous: it fails if the renderer ever regresses to a
    flat list.
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # --- data (JSON): up/down edges carry the expected FQNs ---
    proc = _run_jrag(["hierarchy", _ABS_NOTIFICATION, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"hierarchy failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    other_fqns = {nodes.get(e.get("target"), {}).get("fqn", "") for e in edges}
    # UP: NotificationSender (the interface AbstractNotificationSender implements).
    assert any("NotificationSender" in fqn and "Abstract" not in fqn for fqn in other_fqns), (
        f"expected NotificationSender supertype in {other_fqns}"
    )
    # DOWN: EmailNotificationSender and PushNotificationSender.
    assert any("EmailNotificationSender" in fqn for fqn in other_fqns), (
        f"expected EmailNotificationSender subtype in {other_fqns}"
    )
    assert any("PushNotificationSender" in fqn for fqn in other_fqns), (
        f"expected PushNotificationSender subtype in {other_fqns}"
    )

    # --- rendered structure (text): the ↑/↓ group headers must appear ---
    proc_text = _run_jrag(["hierarchy", _ABS_NOTIFICATION], env=env)
    assert proc_text.returncode == 0, f"text hierarchy failed: {proc_text.stderr}"
    text = proc_text.stdout
    assert "↑ supertypes:" in text, (
        f"expected '↑ supertypes:' header in text output, got:\n{text}"
    )
    assert "↓ subtypes:" in text, (
        f"expected '↓ subtypes:' header in text output, got:\n{text}"
    )
    # The supertypes group must contain NotificationSender and NOT the subtypes.
    up_section = text.split("↓ subtypes:", 1)[0]
    assert "NotificationSender" in up_section and "Abstract" not in up_section.replace(
        "AbstractNotificationSender", ""
    ), f"up section wrong:\n{up_section}"
    # The subtypes group must contain Email + Push.
    dn_section = text.split("↓ subtypes:", 1)[1]
    assert "EmailNotificationSender" in dn_section, f"Email missing from down section:\n{dn_section}"
    assert "PushNotificationSender" in dn_section, f"Push missing from down section:\n{dn_section}"


# ----- Test 6: implementations uses find_implementors -----


def test_implementations_uses_find_implementors(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """implementations on an interface returns its implementors."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["implementations", _PORT_TYPE, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"implementations failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    impl_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    # ConfigurableChatAssignment implements ChatAssignmentPort.
    assert any("ConfigurableChatAssignment" in fqn for fqn in impl_fqns), (
        f"ConfigurableChatAssignment not in implementors {impl_fqns}"
    )


# ----- Test 7: implementations --capability filters (pushed down) -----


def test_implementations_capability_post_filter(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--capability filters implementors (pushed down to find_implementors).

    ADAPTATION: the brief claimed find_implementors has no capability kwarg
    and --capability would be a client-side post-filter. Verified against
    ladybug_queries.py:1051 — the method DOES accept `capability`. So
    --capability is pushed down (matches the global principle "pushed down
    where the method takes it"). The test verifies the filter narrows the
    result: ConfigurableChatAssignment has empty capabilities, so filtering
    by SCHEDULED_TASK returns 0 implementors (vs. 1 without the filter).
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # Without filter: 1 implementor (ConfigurableChatAssignment).
    proc_all = _run_jrag(["implementations", _PORT_TYPE, "--format", "json"], env=env)
    assert proc_all.returncode == 0
    payload_all = json.loads(proc_all.stdout)
    assert len(payload_all.get("edges", [])) >= 1, "expected >=1 implementor without filter"

    # With --capability SCHEDULED_TASK: ConfigurableChatAssignment has caps=[],
    # so the capability filter excludes it -> 0 implementors.
    proc_filtered = _run_jrag(
        ["implementations", _PORT_TYPE, "--capability", "SCHEDULED_TASK", "--format", "json"],
        env=env,
    )
    assert proc_filtered.returncode == 0, (
        f"implementations --capability failed: {proc_filtered.stderr}"
    )
    payload_filtered = json.loads(proc_filtered.stdout)
    assert payload_filtered["status"] == "ok"
    assert len(payload_filtered.get("edges", [])) == 0, (
        f"expected 0 implementors with SCHEDULED_TASK filter, "
        f"got {len(payload_filtered.get('edges', []))}"
    )


# ----- Test 8: subclasses uses find_subclasses -----


def test_subclasses_uses_find_subclasses(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """subclasses on a class returns its subclasses (EXTENDS inbound)."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["subclasses", _ABS_NOTIFICATION, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"subclasses failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    sub_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    # Both Email and Push extend AbstractNotificationSender.
    assert any("EmailNotificationSender" in fqn for fqn in sub_fqns), (
        f"EmailNotificationSender not in subclasses {sub_fqns}"
    )
    assert any("PushNotificationSender" in fqn for fqn in sub_fqns), (
        f"PushNotificationSender not in subclasses {sub_fqns}"
    )


# ----- Test 9: overrides dispatches UP via neighbors(out, OVERRIDES) -----


def test_overrides_dispatches_up_via_neighbors_out_overrides(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """overrides on an overrider method dispatches UP to the declaration.

    The stored OVERRIDES edge runs overrider -> declaration (subtype method ->
    supertype declared method, confirmed in java_ontology.py:251). So
    direction='out' from ConfigurableChatAssignment#requestAssignment returns
    ChatAssignmentPort#requestAssignment (the declaration it overrides).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["overrides", _IMPL_METHOD, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"overrides failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    target_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    assert any("ChatAssignmentPort#requestAssignment" in fqn for fqn in target_fqns), (
        f"expected ChatAssignmentPort#requestAssignment declaration in {target_fqns}"
    )


# ----- Test 10: overridden-by dispatches DOWN via neighbors(in, OVERRIDES) -----


def test_overridden_by_dispatches_down_via_neighbors_in_overrides(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """overridden-by on a declaration dispatches DOWN to its overriders.

    direction='in' on OVERRIDES from ChatAssignmentPort#requestAssignment
    returns ConfigurableChatAssignment#requestAssignment (the method overriding
    it). This is the virtual OVERRIDDEN_BY out direction.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["overridden-by", _PORT_METHOD, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"overridden-by failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    target_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    assert any("ConfigurableChatAssignment#requestAssignment" in fqn for fqn in target_fqns), (
        f"expected ConfigurableChatAssignment#requestAssignment overrider in {target_fqns}"
    )


# ----- Test 11: dependents uses find_injectors -----


def test_dependents_uses_find_injectors(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """dependents on a type returns its injectors (INJECTS inbound)."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["dependents", _PORT_TYPE, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"dependents failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    inj_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    # Three processors inject ChatAssignmentPort in the fixture.
    assert any("ClientMessageProcessor" in fqn for fqn in inj_fqns), (
        f"ClientMessageProcessor not in injectors {inj_fqns}"
    )
    for e in edges:
        assert e.get("edge_type") == "INJECTS", f"expected INJECTS edge, got {e.get('edge_type')}"


# ----- Test 12: impact runs fleet-wide without --service -----


def test_impact_runs_fleet_wide_without_service(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """impact without --service runs the full reverse closure (no microservice predicate)."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["impact", _PORT_TYPE, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"impact failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    # ChatAssignmentPort has 4 impact nodes (3 injectors + 1 implementor).
    assert len(edges) >= 3, f"expected >=3 impact nodes, got {len(edges)}"
    # No warnings when --service is not set.
    assert payload.get("warnings", []) == [], (
        f"expected no warnings without --service, got {payload.get('warnings')}"
    )


# ----- Test 13: impact --service is a post-filter + warning -----


def test_impact_service_post_filter_emits_warning(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """impact --service is a client-side post-filter (no microservice param) + warning."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["impact", _PORT_TYPE, "--service", "chat-core", "--format", "json"], env=env
    )
    assert proc.returncode == 0, (
        f"impact --service failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    warnings = payload.get("warnings", [])
    assert any("--service" in w and "post-filter" in w for w in warnings), (
        f"expected --service post-filter warning, got warnings={warnings}"
    )
    # All returned impact nodes should be from chat-core (post-filter applied).
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    for e in edges:
        node = nodes.get(e.get("target"), {})
        svc = (node.get("microservice") or "").strip()
        # The post-filter keeps only chat-core matches; skip root (the target itself).
        if svc:
            assert svc == "chat-core", (
                f"expected chat-core after post-filter, got microservice={svc!r} on {node.get('fqn')}"
            )


# ----- Test 14: decompose renders the role waterfall -----


def test_decompose_renders_role_waterfall(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """decompose on an entrypoint returns the role-waterfall stages AND renders them.

    ChatIngressController (CONTROLLER) -> stage 1 with COMPONENT/SERVICE roles.

    Asserts BOTH the data (JSON: stage field + reached engine components) AND
    the rendered structure (text: `stage 0 (seed):` and `stage 1 ...:` group
    headers that `_render_traversal` emits for stage-carrying edges). The text
    assertion is non-vacuous: it fails if the renderer regresses to flat.
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # --- data (JSON): stages + reached engine components ---
    proc = _run_jrag(["decompose", _INGRESS_CTRL, "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"decompose failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id"
    edges = payload.get("edges", [])
    nodes = payload.get("nodes", {})
    # The root (ChatIngressController) should be present as the seed.
    root_node = nodes.get(payload["root"], {})
    assert "ChatIngressController" in root_node.get("fqn", ""), (
        f"expected ChatIngressController as root, got {root_node}"
    )
    # At least one non-root symbol reached (stage 1).
    assert len(edges) >= 1, f"expected >=1 flow edge, got {edges}"
    # Stage index is carried on each edge row (role-waterfall rendering hint).
    assert all("stage" in e for e in edges), (
        f"expected 'stage' field on every decompose edge, got {edges[:2]}"
    )
    reached_fqns = [nodes.get(e.get("target"), {}).get("fqn", "") for e in edges]
    # Stage 1 includes the engine components (COMPONENT role) — at least one
    # processor/publisher/ratelimiter should be reached from the controller.
    assert any(
        "Processor" in fqn or "Publisher" in fqn or "RateLimiter" in fqn
        for fqn in reached_fqns
    ), f"expected engine component in reached fqns {reached_fqns}"

    # --- rendered structure (text): the stage group headers must appear ---
    proc_text = _run_jrag(["decompose", _INGRESS_CTRL], env=env)
    assert proc_text.returncode == 0, f"text decompose failed: {proc_text.stderr}"
    text = proc_text.stdout
    # stage 0 is the seed (the entrypoint itself).
    assert "stage 0 (seed):" in text, (
        f"expected 'stage 0 (seed):' header in text output, got:\n{text}"
    )
    # At least one later stage header must be present (the waterfall has >=2 stages).
    assert "stage 1" in text, (
        f"expected 'stage 1' header in text output, got:\n{text}"
    )
    # Stage 1 on this fixture mixes SERVICE + COMPONENT, so the renderer must
    # list BOTH roles in the header (`stage 1 (component, service):`) rather
    # than dropping the role label on its busiest stage. The role allow-list is
    # the whole point of a role-waterfall — hiding it where it matters most is
    # the gap this closes.
    assert "stage 1 (" in text and "stage 1" in text, (
        f"expected a role-labelled 'stage 1 (...):' header, got:\n{text}"
    )
    assert "component" in text and "service" in text, (
        f"expected both 'component' and 'service' roles in the stage-1 header:\n{text}"
    )
    # The seed stage must list the controller; a later stage lists engine components.
    seed_section = text.split("stage 1", 1)[0]
    assert "ChatIngressController" in seed_section, (
        f"expected ChatIngressController in seed section:\n{seed_section}"
    )


# ----- Test 15: flow outbound is intra-service on the fixture (data property) -----


def test_flow_outbound_intra_service_on_fixture(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """flow on a Route returns outbound CALLS hops (a data property, not a query constraint).

    trace_request_flow has no microservice predicate (verified at
    ladybug_queries.py:1810). The fixture's CALLS edges span microservices
    (e.g. the chat-assign handler reaches chat-core DTO methods like
    AssignmentRequest#getEpkId), which PROVES the query applies no service
    filter — a query constraint would have dropped the chat-core endpoints.
    This test validates the fixture's indexed CALLS edges, not a constraint.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["flow", "/chat/assign", "--format", "json"], env=env)
    assert proc.returncode == 0, (
        f"flow failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    # Root must be the Route.
    assert payload.get("root"), "expected root id (the Route)"
    nodes = payload.get("nodes", {})
    root_node = nodes.get(payload["root"], {})
    assert root_node.get("kind") == "route", f"expected route root, got {root_node}"
    # Outbound CALLS edges must be present (the fixture indexed ~28).
    edges = payload.get("edges", [])
    outbound = [e for e in edges if e.get("edge_type") == "CALLS"]
    assert len(outbound) >= 1, (
        f"expected >=1 outbound CALLS edge, got {len(outbound)} (edges={edges})"
    )
    # Data-property assertion: the endpoint microservices SPAN more than one
    # value (chat-assign handler + chat-core DTOs), proving the query applies
    # NO microservice filter. This is the index-time data property — CALLS
    # edges are intra-codebase (java_ontology.py:286), not intra-service.
    endpoint_services = set()
    for e in outbound:
        ep = nodes.get(e.get("target"), {})
        svc = (ep.get("microservice") or "").strip()
        if svc:
            endpoint_services.add(svc)
    assert "chat-assign" in endpoint_services, (
        f"expected chat-assign endpoints in outbound, got services={endpoint_services}"
    )
    # The contracts DTOs live under chat-core; their presence proves no service
    # filter was applied (a constraint would have dropped them).
    assert "chat-core" in endpoint_services, (
        f"expected chat-core endpoints (cross-service, no filter) in outbound, "
        f"got services={endpoint_services}"
    )


def test_flow_follows_kafka_topic_on_fixture(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """flow resolves a Kafka topic to its Route AND follows it (async edges).

    Regression: ``_resolve_route_candidates`` matched only on path, so a
    ``kafka_topic`` Route (name in ``topic``, ``path=''``) was unresolvable and
    ``jrag flow <topic>`` returned 'none'. Resolution alone is not enough — this
    test also asserts the follow graph is non-empty and carries an
    ``ASYNC_CALLS`` edge, proving the kafka-specific inbound arm (topic-matched
    Producer) actually fires once the route resolves.

    ``banking.chat.incoming`` is the canonical dual-sided topic: produced by
    ``FollowUpKafkaPublisher`` (inbound ``ASYNC_CALLS``) and consumed by
    ``ChatKafkaListener`` (outbound ``CALLS`` to ``orchestrationService.handle``).
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(
        ["flow", "banking.chat.incoming", "--format", "json"], env=env
    )
    assert proc.returncode == 0, (
        f"flow on kafka topic failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok", f"expected ok, got {payload}"
    assert payload.get("root"), "expected root id (the kafka_topic Route)"
    root_node = payload.get("nodes", {}).get(payload["root"], {})
    assert root_node.get("kind") == "route", f"expected route root, got {root_node}"
    # The follow graph must be non-empty AND carry a kafka async inbound edge —
    # this is the part that proves "flow follows kafka", not just "resolves it".
    edges = payload.get("edges", [])
    assert edges, f"expected non-empty follow graph for a kafka topic, got edges={edges}"
    assert any(e.get("edge_type") == "ASYNC_CALLS" for e in edges), (
        f"expected an ASYNC_CALLS (topic-matched Producer) inbound edge, got {edges}"
    )


def test_flow_depth_flag_and_max_hops_alias(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """flow uses --depth (consistent with callers/callees/impact/decompose).

    --max-hops remains as a hidden back-compat alias (same dest). Both must be
    accepted, produce the same traversal, and --depth must actually change the
    traversal depth (more hops => strictly more CALLS edges).
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # --depth is the primary flag now.
    proc_depth = _run_jrag(
        ["flow", "/chat/assign", "--depth", "2", "--format", "json"], env=env
    )
    assert proc_depth.returncode == 0, (
        f"flow --depth failed: rc={proc_depth.returncode}\nstdout={proc_depth.stdout}"
    )
    payload_depth = json.loads(proc_depth.stdout)
    assert payload_depth["status"] == "ok", f"expected ok, got {payload_depth}"

    # --max-hops is the hidden alias; must still be accepted (back-compat).
    proc_alias = _run_jrag(
        ["flow", "/chat/assign", "--max-hops", "2", "--format", "json"], env=env
    )
    assert proc_alias.returncode == 0, (
        f"flow --max-hops alias failed: rc={proc_alias.returncode}\nstdout={proc_alias.stdout}"
    )
    payload_alias = json.loads(proc_alias.stdout)
    assert payload_alias["status"] == "ok", f"expected ok, got {payload_alias}"
    # Same dest -> same traversal (depth 2 in both cases).
    assert payload_alias.get("root") == payload_depth.get("root"), (
        "expected identical root id for --depth and --max-hops"
    )

    # --depth must actually affect the traversal: depth 1 yields strictly fewer
    # outbound CALLS edges than depth 5 on this fixture (~3 vs ~20).
    proc_shallow = _run_jrag(
        ["flow", "/chat/assign", "--depth", "1", "--format", "json"], env=env
    )
    proc_deep = _run_jrag(
        ["flow", "/chat/assign", "--depth", "5", "--format", "json"], env=env
    )
    shallow = json.loads(proc_shallow.stdout)
    deep = json.loads(proc_deep.stdout)
    assert len(shallow.get("edges", [])) < len(deep.get("edges", [])), (
        f"--depth should change traversal size; depth=1 -> {len(shallow.get('edges', []))}, "
        f"depth=5 -> {len(deep.get('edges', []))}"
    )


def test_callers_topic_disambiguates_with_kind(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """A dual-sided topic resolves to BOTH a Producer and a Route -> ambiguous
    without --kind; --kind route collapses to one and runs find_route_callers.

    Behavior note (from enabling r.topic resolution): ``callers <topic>`` with no
    --kind now searches all kinds, and a topic with both a Producer and a server
    Route is genuinely ambiguous (previously only the Producer matched, which
    then errored because callers only accepts Symbol/Route roots — jrag.py:2371).
    ``--kind route`` is the disambiguation that yields a working callers run.
    (--kind producer resolves to the Producer but callers rejects a Producer
    root; use the ``producers`` command for the producer view.)
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # No --kind: Producer + Route both match -> ambiguous (no traversal).
    proc_both = _run_jrag(
        ["callers", "banking.chat.incoming", "--format", "json"], env=env
    )
    payload_both = json.loads(proc_both.stdout)
    assert payload_both["status"] == "ambiguous", (
        f"expected ambiguous for dual-sided topic without --kind, got {payload_both.get('status')}"
    )
    assert len(payload_both.get("candidates", [])) >= 2, (
        f"expected >=2 candidates (producer + route), got {payload_both.get('candidates')}"
    )

    # --kind route: resolves to the single server Route and runs find_route_callers.
    proc_route = _run_jrag(
        ["callers", "banking.chat.incoming", "--kind", "route", "--format", "json"],
        env=env,
    )
    payload_route = json.loads(proc_route.stdout)
    assert payload_route["status"] == "ok", (
        f"expected --kind route to resolve and run, got {payload_route.get('status')}"
    )
    assert payload_route.get("root"), "expected a resolved root for --kind route"


# ----- Test 16: traversal resolve-ambiguous stops (no auto-pick) -----


def test_traversal_resolve_ambiguous_stops(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """An ambiguous resolve query returns candidates and stops (no traversal).

    'requestAssignment' resolves to 'many' (the port method + the impl method
    both contain that name). The traversal must NOT auto-pick; it returns the
    ambiguous envelope with candidates.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["callers", "requestAssignment", "--format", "json"], env=env)
    # Ambiguous returns rc=0 (per the inspect/resolve convention).
    assert proc.returncode == 0, (
        f"ambiguous resolve should return 0, got {proc.returncode}\nstdout={proc.stdout}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ambiguous", (
        f"expected ambiguous for 'requestAssignment', got {payload.get('status')}: {payload}"
    )
    assert len(payload.get("candidates", [])) >= 2, (
        f"expected >=2 candidates, got {payload.get('candidates')}"
    )
    # No traversal edges should be produced.
    assert payload.get("edges", []) == [], (
        f"expected no edges on ambiguous stop, got {payload.get('edges')}"
    )


# ----- Test 17: --offset is rejected on every traversal -----


def test_traversal_rejects_offset() -> None:
    """--offset is NOT registered on any traversal subparser."""
    env = os.environ.copy()
    traversals = [
        "callers", "callees", "hierarchy", "implementations", "subclasses",
        "overrides", "overridden-by", "dependents", "impact", "decompose", "flow",
    ]
    for cmd in traversals:
        proc = _run_jrag([cmd, "somequery", "--offset", "5"], env=env)
        assert proc.returncode != 0, f"{cmd} --offset should be rejected (rc!=0)"
        assert (
            "unrecognized arguments: --offset" in proc.stderr or "usage:" in proc.stderr
        ), f"{cmd}: expected usage error, got stderr={proc.stderr!r}"


# ----- Test 18: inapplicable --service/--module/--limit surface warnings -----
# (Fix 3 + Fix 4 follow-up: plan principle "inapplicable flags never silently ignored".


def test_inapplicable_flags_emit_warnings(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """--service/--module on hierarchy/overrides/overridden-by/flow and --limit
    on decompose surface a warnings[] entry rather than being silently dropped.

    These commands walk structural edges or carry no microservice predicate;
    --service/--module cannot be applied. decompose's real cap is --max-stage,
    not --limit. Each must emit a warning naming the flag so the agent gets a
    signal (plan principle: inapplicable flags never silently ignored).
    """
    env = _env_for(corpus_root, ladybug_db_path)

    # hierarchy: --service/--module not applied (structural EXTENDS/IMPLEMENTS).
    proc = _run_jrag(
        ["hierarchy", _ABS_NOTIFICATION, "--service", "chat-core", "--module", "chat-engine", "--format", "json"],
        env=env,
    )
    assert proc.returncode == 0, f"hierarchy failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    warnings = payload.get("warnings", [])
    assert any("--service is not applied" in w for w in warnings), (
        f"hierarchy: expected --service warning, got {warnings}"
    )
    assert any("--module is not applied" in w for w in warnings), (
        f"hierarchy: expected --module warning, got {warnings}"
    )

    # overrides: --service not applied (structural method-to-method edge).
    proc = _run_jrag(
        ["overrides", _IMPL_METHOD, "--service", "chat-core", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"overrides failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert any("--service is not applied" in w for w in payload.get("warnings", [])), (
        f"overrides: expected --service warning, got {payload.get('warnings')}"
    )

    # overridden-by: --module not applied.
    proc = _run_jrag(
        ["overridden-by", _PORT_METHOD, "--module", "chat-engine", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"overridden-by failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert any("--module is not applied" in w for w in payload.get("warnings", [])), (
        f"overridden-by: expected --module warning, got {payload.get('warnings')}"
    )

    # flow: --service not applied (no microservice predicate; data property).
    proc = _run_jrag(
        ["flow", "/chat/assign", "--service", "chat-assign", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"flow failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert any("--service is not applied" in w for w in payload.get("warnings", [])), (
        f"flow: expected --service warning, got {payload.get('warnings')}"
    )

    # decompose: --limit (non-default) does not apply; --max-stage is the knob.
    proc = _run_jrag(
        ["decompose", _INGRESS_CTRL, "--limit", "5", "--format", "json"], env=env
    )
    assert proc.returncode == 0, f"decompose --limit failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert any("--limit does not apply to decompose" in w for w in payload.get("warnings", [])), (
        f"decompose: expected --limit warning, got {payload.get('warnings')}"
    )

    # Sanity: decompose with the DEFAULT --limit (20, not explicitly set) is silent.
    proc_default = _run_jrag(
        ["decompose", _INGRESS_CTRL, "--format", "json"], env=env
    )
    assert proc_default.returncode == 0
    payload_default = json.loads(proc_default.stdout)
    assert not any("--limit" in w for w in payload_default.get("warnings", [])), (
        f"decompose default should not warn about --limit, got {payload_default.get('warnings')}"
    )


# ===== Phase 3 regression: Java-kind enforcement (T6) =====


def test_implementations_rejects_class_root(corpus_root: Path, ladybug_db_path: Path) -> None:
    """implementations expects an INTERFACE; a class root must error (not silently
    return empty). Graph kind=symbol covers class+interface+method, so the label
    guard alone let a class through (Phase 3 T6)."""
    env = _env_for(corpus_root, ladybug_db_path)
    # _ABS_NOTIFICATION is an abstract CLASS (symbol_kind=class).
    proc = _run_jrag(["implementations", _ABS_NOTIFICATION, "--format", "json"], env=env)
    assert proc.returncode == 2, (
        f"implementations <class> should error: rc={proc.returncode}\nstdout={proc.stdout}"
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error", f"expected error, got {payload}"
    assert "Java kind" in payload.get("message", ""), (
        f"expected Java-kind message, got {payload.get('message')!r}"
    )


def test_subclasses_rejects_method_root(corpus_root: Path, ladybug_db_path: Path) -> None:
    """subclasses expects a class/interface; a method root must error."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["subclasses", _SVC_ASSIGN, "--format", "json"], env=env)
    assert proc.returncode == 2, f"subclasses <method> should error: rc={proc.returncode}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert "Java kind" in payload.get("message", "")


def test_overrides_rejects_class_root(corpus_root: Path, ladybug_db_path: Path) -> None:
    """overrides expects a method; a class root must error."""
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["overrides", _ABS_NOTIFICATION, "--format", "json"], env=env)
    assert proc.returncode == 2, f"overrides <class> should error: rc={proc.returncode}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert "Java kind" in payload.get("message", "")


# ===== Phase 3 regression: callee edge dedup (T7) =====


def test_callees_dedupes_duplicate_targets(corpus_root: Path, ladybug_db_path: Path) -> None:
    """callees must not emit duplicate edges for the same callee.

    ChatManagementService#assign reaches some callees via multiple call sites
    (find_callees emits one CallEdge per call site). Without dedup the JSON
    carried repeats; the id-free renderer then showed the same target twice.
    Phase 3 T7 dedupes by (other_id, edge_type) and drops empty other_id.
    """
    env = _env_for(corpus_root, ladybug_db_path)
    proc = _run_jrag(["callees", _SVC_ASSIGN, "--format", "json"], env=env)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    edges = payload.get("edges", [])
    targets = [e.get("target") for e in edges]
    # No None/empty targets survive (phantom edges dropped).
    assert all(targets), f"expected every edge to have a target, got {targets}"
    # No duplicate targets.
    dupes = [t for t in targets if targets.count(t) > 1]
    assert not dupes, f"expected no duplicate callee targets, got duplicates: {set(dupes)}"
