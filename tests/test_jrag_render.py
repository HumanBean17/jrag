"""Tests for java_codebase_rag.jrag_render (PR-JRAG-1a).

Pure unit tests for the text renderer. Constructs envelopes directly (no graph
fixtures) so the render shapes are pinned independently of resolve / traversal
backends.
"""
from __future__ import annotations

import json

from java_codebase_rag.jrag_envelope import Envelope, simple_name
from java_codebase_rag.jrag_render import render, tiered_name


# ----- Test 11: listing omits FQN -----


def test_render_listing_omits_fqn() -> None:
    """Listing output is `name  @service` only; FQN is never rendered."""
    env = Envelope(
        status="ok",
        nodes={
            "sym:1": {"fqn": "com.foo.Bar.doStuff", "microservice": "foo-svc"},
            "sym:2": {"fqn": "com.foo.Baz.handle", "microservice": "bar-svc"},
        },
    )
    out = render(env, fmt="text", noun="matches")
    assert "com.foo.Bar.doStuff" not in out, f"FQN leaked into listing: {out!r}"
    assert "com.foo.Baz.handle" not in out, f"FQN leaked into listing: {out!r}"
    lines = out.splitlines()
    assert "doStuff  @foo-svc" in lines
    assert "handle  @bar-svc" in lines


def test_render_listing_zero_nodes_emits_zero_line() -> None:
    env = Envelope(status="ok", nodes={})
    out = render(env, fmt="text", noun="matches")
    assert out.strip() == "0 matches"


def test_display_name_handles_routes_clients_producers() -> None:
    """display_name picks the identifying field per node kind (not FQN-only).

    Regression for routes rendering blank: routes have ``path``/``method``, not
    ``fqn``; the old ``simple_name`` returned ``''`` and listings showed a bare
    ``@service`` with no name. The same gap affected clients/producers.
    """
    from java_codebase_rag.jrag_render import display_name

    # Route: METHOD path (no FQN at all).
    route = {"kind": "http_endpoint", "method": "POST", "path": "/api/chat/send"}
    assert display_name(route) == "POST /api/chat/send"
    # Route with no method: bare path.
    assert display_name({"kind": "http_endpoint", "path": "/health"}) == "/health"
    # Client: member simple-name -> target service.
    client = {
        "client_kind": "feign_method",
        "target_service": "chat-assign",
        "member_fqn": "com.bchat.Proc.send",
    }
    assert display_name(client) == "send → chat-assign"
    # Producer: member simple-name -> topic.
    producer = {
        "producer_kind": "kafka_send",
        "topic": "chat-messages",
        "member_fqn": "com.bchat.Prod.send",
    }
    assert display_name(producer) == "send → chat-messages"
    # Symbol fallback unchanged.
    assert display_name({"fqn": "com.foo.Bar"}) == "Bar"
    # Topic-only node (topics command grouping).
    assert display_name({"topic": "chat-messages"}) == "chat-messages"


def test_render_listing_routes_shows_method_path_not_blank() -> None:
    """A route listing row renders `METHOD path  @service`, never a bare `@service`.

    Regression: routes carry no FQN; before ``display_name`` the listing emitted
    ``  @chat-core`` with a blank name (confusing — the user couldn't tell
    routes apart across services).
    """
    env = Envelope(
        status="ok",
        nodes={
            "r:1": {
                "kind": "http_endpoint",
                "method": "POST",
                "path": "/api/chat/send",
                "microservice": "chat-core",
            },
            "r:2": {
                "kind": "http_endpoint",
                "method": "GET",
                "path": "/api/chat/history",
                "microservice": "chat-assign",
            },
        },
    )
    out = render(env, fmt="text", noun="route")
    lines = out.splitlines()
    assert "POST /api/chat/send  @chat-core" in lines, f"route row missing: {out!r}"
    assert "GET /api/chat/history  @chat-assign" in lines, f"route row missing: {out!r}"
    # No bare `@service` line (the bug signature: blank name + service suffix).
    assert not any(line.strip().startswith("@") for line in lines), (
        f"blank-name listing line leaked: {out!r}"
    )


# ----- Test 12: traversal conf: only on CALLS-family -----


def test_render_traversal_conf_only_on_calls() -> None:
    """conf=N.NN is rendered only for CALLS / HTTP_CALLS / ASYNC_CALLS edges."""
    env = Envelope(
        status="ok",
        root="sym:0",
        nodes={
            "sym:0": {"fqn": "com.foo.Caller.call", "microservice": "svc"},
            "sym:1": {"fqn": "com.foo.Callee.a", "microservice": "svc"},
            "sym:2": {"fqn": "com.foo.Parent.b", "microservice": "svc"},
        },
        edges=[
            {
                "edge_type": "CALLS",
                "other_id": "sym:1",
                "confidence": 0.92,
            },
            {
                "edge_type": "OVERRIDES",
                "other_id": "sym:2",
                "confidence": 0.8,  # MUST NOT be rendered for OVERRIDES
            },
        ],
    )
    out = render(env, fmt="text", noun="callees")
    # The CALLS edge row carries conf=0.92.
    assert "conf=0.92" in out, f"missing conf on CALLS edge: {out!r}"
    # The OVERRIDES edge row has no conf=, despite carrying a confidence value.
    overrides_line = next(line for line in out.splitlines() if "Parent" in line or "b @" in line)
    assert "conf=" not in overrides_line, f"conf leaked onto OVERRIDES edge: {overrides_line!r}"


def test_render_traversal_root_line_present() -> None:
    env = Envelope(
        status="ok",
        root="sym:0",
        nodes={"sym:0": {"fqn": "com.foo.Caller.call", "microservice": "svc"}},
        edges=[],
    )
    out = render(env, fmt="text", noun="callees")
    assert out.splitlines()[0].startswith("root: ")


def test_render_overrides_does_not_mislabel_as_supertypes() -> None:
    """Regression (review finding D): overrides/overridden-by edges must NOT
    render under `↑ supertypes:`/`↓ subtypes:` hierarchy headers.

    The producers used to set ``direction='up'/'down'`` on the edge rows, which
    tripped the renderer's ``has_direction`` guard and routed them into the
    hierarchy branch. The fix dropped the direction key; overrides is a flat
    list. Tests previously asserted JSON only, so the mis-label was invisible.
    """
    env = Envelope(
        status="ok",
        root="sym:0",
        nodes={
            "sym:0": {"fqn": "com.foo.Impl", "microservice": "svc"},
            "sym:1": {"fqn": "com.foo.Base", "microservice": "svc"},
        },
        edges=[{"other_id": "sym:1", "edge_type": "OVERRIDES"}],
    )
    out = render(env, fmt="text", noun="overrides")
    assert "supertype" not in out.lower(), f"overrides mislabeled as supertypes: {out!r}"
    assert "subtype" not in out.lower(), f"overrides mislabeled as subtypes: {out!r}"
    # The overridden declaration IS rendered (flat row), not swallowed.
    assert "Base" in out, f"overrides target not rendered: {out!r}"


def test_render_warnings_visible_in_text() -> None:
    """Regression (review finding F): warnings[] render as `warning:` lines in
    text mode.

    Previously warnings were JSON-only — the listing/inspect/traversal shapes
    never emitted them, so the 'inapplicable flags never silently ignored' spec
    was effectively unenforced for text consumers. The renderer now appends one
    ``warning:`` line per warning after the body.
    """
    env = Envelope(
        status="ok",
        nodes={"sym:1": {"fqn": "com.foo.Bar", "microservice": "svc"}},
        warnings=["--service is not applied on this command", "--limit is not applied on this command"],
    )
    out = render(env, fmt="text", noun="matches")
    assert "warning: --service is not applied on this command" in out, (
        f"warning not rendered in text mode: {out!r}"
    )
    assert "warning: --limit is not applied on this command" in out, (
        f"second warning missing: {out!r}"
    )


# ----- Test 13: inspect edge_summary alphabetical -----


def test_render_inspect_edge_summary_alphabetical() -> None:
    """Inspect renders ALL dict keys alphabetically; edge_summary is indented + sorted.

    Inspect is now declared via the explicit ``shape="inspect"`` hint (no
    longer inferred from node contents - a listing node with dict-valued
    fields must NOT route to inspect). Callers like ``jrag status`` and the
    future ``jrag inspect`` declare their shape; the renderer does not guess.
    """
    env = Envelope(
        status="ok",
        nodes={
            "sym:1": {
                # Top-level keys intentionally unsorted.
                "fqn": "com.foo.Bar",
                "kind": "class",
                "name": "Bar",
                "role": "SERVICE",
                "edge_summary": {
                    # Edge summary keys intentionally unsorted.
                    "OVERRIDES": {"in": 0, "out": 3},
                    "CALLS": {"in": 5, "out": 2},
                    "EXTENDS": {"in": 0, "out": 1},
                },
            }
        },
    )
    out = render(env, fmt="text", noun="inspect", shape="inspect")
    lines = out.splitlines()
    # Top-level keys appear in alphabetical order.
    keys_in_output = [ln.split(":", 1)[0] for ln in lines if ":" in ln and not ln.startswith(" ")]
    # Filter out only the known top-level keys.
    expected_top = ["edge_summary", "fqn", "kind", "name", "role"]
    assert keys_in_output == expected_top, f"top keys not alphabetical: {keys_in_output}"
    # edge_summary line is followed by sorted indented keys.
    summary_idx = next(i for i, ln in enumerate(lines) if ln.startswith("edge_summary:"))
    summary_lines = [ln.strip() for ln in lines[summary_idx + 1 :] if ln.startswith("  ")]
    summary_keys = [ln.split(":", 1)[0] for ln in summary_lines]
    assert summary_keys == ["CALLS", "EXTENDS", "OVERRIDES"], f"summary not sorted: {summary_keys}"


def test_render_listing_with_dict_valued_node_does_not_route_to_inspect() -> None:
    """A listing node carrying dict-valued fields (typical after .model_dump())
    must NOT silently route to inspect - dispatch is explicit via shape hint.
    Regression for the structural-dispatch foot-gun flagged in re-review.
    """
    env = Envelope(
        status="ok",
        nodes={
            "sym:1": {
                "fqn": "com.foo.Bar.doStuff",
                "microservice": "svc",
                # Symbol nodes typically carry dict-valued fields after
                # .model_dump(): source_range, annotations, capabilities, etc.
                "annotations": {"@Override": True},
                "source_range": {"start": 1, "end": 10},
            }
        },
    )
    out = render(env, fmt="text", noun="matches")
    # Listing shape: FQN is omitted (test 11 contract); only name + @service.
    assert "com.foo.Bar.doStuff" not in out, (
        f"listing leaked FQN - routed to inspect by mistake: {out!r}"
    )
    assert "doStuff  @svc" in out.splitlines()


# ----- Test 14: ambiguous lists reason, no file/score -----


def test_render_ambiguous_lists_reason_no_file() -> None:
    """Ambiguous candidates carry `reason`; NO file or score columns."""
    env = Envelope(
        status="ambiguous",
        candidates=[
            {
                "id": "sym:1",
                "fqn": "com.foo.Bar.doStuff",
                "name": "doStuff",
                "microservice": "foo",
                "reason": "fqn_suffix",
            },
            {
                "id": "sym:2",
                "fqn": "com.foo.Baz.doStuff",
                "name": "doStuff",
                "microservice": "bar",
                "reason": "short_name",
            },
        ],
    )
    out = render(env, fmt="text", noun="doStuff")
    assert "ambiguous" in out
    assert "fqn_suffix" in out
    assert "short_name" in out
    # No file path or score leaks into ambiguous output.
    assert ".java" not in out
    assert "score" not in out.lower()


# ----- Test 15: zero results vs not_found distinct -----


def test_render_zero_results_vs_not_found_distinct() -> None:
    """Zero-result ok envelope -> '0 <noun>'; not_found envelope -> 'not found: <msg>'."""
    zero_env = Envelope(status="ok", nodes={}, root="sym:1")
    not_found_env = Envelope(status="not_found", message="No matches for 'foo'.")

    zero_out = render(zero_env, fmt="text", noun="callees")
    nf_out = render(not_found_env, fmt="text", noun="callees")

    # Zero results line starts with "0 <noun>".
    assert "0 callees" in zero_out, f"zero-results missing '0 <noun>': {zero_out!r}"
    assert "not found" not in zero_out, f"zero-results looks like not_found: {zero_out!r}"

    # not_found line is "not found: <msg>".
    assert nf_out.startswith("not found:"), f"not_found shape wrong: {nf_out!r}"
    assert "No matches for 'foo'." in nf_out
    assert "0 callees" not in nf_out, f"not_found looks like zero-results: {nf_out!r}"


# ----- Tests 16 / 17: truncated hint -----


def test_render_truncated_narrow_query_for_non_offset_commands() -> None:
    """Non-offset commands (traversal/listing) emit 'narrow your query'."""
    env = Envelope(status="ok", truncated=True, nodes={"sym:1": {"fqn": "com.foo.Bar"}})
    out = render(env, fmt="text", noun="callers", next_offset=None)
    assert "truncated: more results — narrow your query" in out
    assert "--offset" not in out, f"offset hint leaked on non-offset command: {out!r}"


def test_render_truncated_offset_hint_for_offset_commands() -> None:
    """Offset commands (find/search) emit 'use --offset <next_offset>'."""
    env = Envelope(status="ok", truncated=True, nodes={"sym:1": {"fqn": "com.foo.Bar"}})
    out = render(env, fmt="text", noun="find", next_offset=40)
    assert "truncated: more results — use --offset 40" in out


# ----- Test 18: json emits envelope verbatim -----


def test_render_json_emits_envelope_verbatim() -> None:
    env = Envelope(
        status="ok",
        root="sym:1",
        nodes={"sym:1": {"fqn": "com.foo.Bar"}},
        warnings=["partial"],
    )
    out = render(env, fmt="json")
    # Output is exactly json.dumps(env.to_dict()) — no extra decoration.
    assert out == env.to_json()
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    assert parsed["root"] == "sym:1"
    assert parsed["nodes"] == {"sym:1": {"fqn": "com.foo.Bar"}}
    assert parsed["warnings"] == ["partial"]


# ----- Test 19: simple_name derived from FQN (NodeRef has no `name`) -----


def test_simple_name_derived_from_fqn() -> None:
    """NodeRef carries no `name` field; simple_name derives a short label from FQN.

    A pydantic NodeRef crosses the model_dump boundary as a dict, then
    simple_name extracts the simple name from the FQN.
    """
    from graph_types import NodeRef

    ref = NodeRef(id="sym:1", kind="symbol", fqn="com.example.MyClass.handle")
    row = ref.model_dump()
    assert "name" not in row or row.get("name") is None
    assert simple_name(row) == "handle"
    assert simple_name({"fqn": "com.foo.Bar"}) == "Bar"
    assert simple_name({"fqn": ""}) == ""
    assert simple_name({}) == ""


# ----- Bonus: tiered_name tiers -----


def test_tiered_name_prefers_name_at_service() -> None:
    nodes = {"sym:1": {"fqn": "com.foo.Bar.doStuff", "microservice": "foo-svc"}}
    assert tiered_name("sym:1", nodes) == "doStuff @foo-svc"


def test_tiered_name_falls_back_to_fqn_when_no_service() -> None:
    nodes = {"sym:1": {"fqn": "com.foo.Bar.doStuff"}}
    # No service: just the simple name (still derived from FQN).
    assert tiered_name("sym:1", nodes) == "doStuff"


def test_tiered_name_unknown_id_returns_id() -> None:
    assert tiered_name("sym:unknown", {}) == "sym:unknown"
