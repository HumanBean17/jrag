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
    # Route rows are prefixed with a [http]/[kafka] type tag so an agent can tell
    # HTTP endpoints apart from Kafka topics in a mixed listing.
    assert "[http]  POST /api/chat/send  @chat-core" in lines, f"route row missing: {out!r}"
    assert "[http]  GET /api/chat/history  @chat-assign" in lines, f"route row missing: {out!r}"
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
    out = render(env, fmt="text", noun="inspect", shape="inspect", detail="full")
    lines = out.splitlines()
    # Top-level keys appear in alphabetical order.
    keys_in_output = [ln.split(":", 1)[0] for ln in lines if ":" in ln and not ln.startswith(" ")]
    # Filter out only the known top-level keys.
    expected_top = ["edge_summary", "fqn", "kind", "name", "role"]
    assert keys_in_output == expected_top, f"top keys not alphabetical: {keys_in_output}"
    # edge_summary recurses (dict-of-dicts): each edge type is an indent-2 header
    # (alphabetical) with its in/out counts indented one more level beneath.
    summary_idx = next(i for i, ln in enumerate(lines) if ln.startswith("edge_summary:"))
    header_lines = [
        ln for ln in lines[summary_idx + 1:]
        if ln.startswith("  ") and not ln.startswith("    ")
    ]
    header_keys = [ln.split(":", 1)[0].strip() for ln in header_lines]
    assert header_keys == ["CALLS", "EXTENDS", "OVERRIDES"], f"summary not sorted: {header_keys}"
    # Each edge-type header is followed by its nested in/out counts.
    assert "    in: 5" in lines and "    out: 2" in lines, f"nested counts missing: {out!r}"


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


# ----- Test 18: json path (now via projection — PR-JRAG-6) -----


def test_render_json_full_is_idfree_envelope_for_projection_invariant_data() -> None:
    """``render(fmt="json")`` now projects the envelope to the requested detail
    level (orthogonal to text). For projection-invariant data (only identity
    fields) and ``detail="full"``, the output still equals ``env.to_json()`` —
    pinning that the json path is a plain ``json.dumps`` of the projected dict
    with no extra decoration. Field-set trimming itself is pinned by the
    orthogonality test below.
    """
    env = Envelope(
        status="ok",
        root="sym:1",
        nodes={"sym:1": {"id": "sym:1", "fqn": "com.foo.Bar"}},
        warnings=["partial"],
    )
    out = render(env, fmt="json", detail="full")
    assert out == env.to_json()
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    # root + node key are the FQN (the node's natural key), NOT the graph id;
    # the internal ``id`` field is stripped at the boundary.
    assert parsed["root"] == "com.foo.Bar"
    assert parsed["nodes"] == {"com.foo.Bar": {"fqn": "com.foo.Bar"}}
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


# ----- PR-JRAG-6: --detail orthogonality (text & json share the field set) -----


def _search_listing_env() -> Envelope:
    """A search-results envelope carrying score + snippet + empty fields."""
    return Envelope(
        status="ok",
        nodes={
            "chunk:1": {
                "id": "chunk:1",
                "kind": "search_hit",
                "fqn": "com.foo.Bar",
                "name": "Bar",
                "microservice": "chat",
                "module": "core",
                "role": "SERVICE",
                "score": 0.91,
                "snippet": "public class Bar {\n  void x();\n}",
                "symbol_id": None,  # empty — must vanish in json
            }
        },
    )


def test_json_and_text_share_field_set_at_each_detail() -> None:
    """Core orthogonality: at a given detail level, the SAME node keys appear
    behind both ``--format json`` and ``--format text`` (the projector is the
    single seam). The text line shows identity; the json dict shows the exact
    projected key set. This is the whole point of PR-JRAG-6.
    """
    env = _search_listing_env()
    for detail, expected_keys in (
        # ``id`` is stripped at every level (graph ids are not agent-facing);
        # nodes are keyed by their natural key (FQN), so look up "com.foo.Bar".
        # ``score`` is in the brief set: for ranked result sets (search) the
        # score IS the point, so it is identity-adjacent and shown at every
        # tier (listing/traversal rows built from NodeRef carry no ``score``
        # field, so this only affects search hits).
        ("brief", {"kind", "fqn", "name", "microservice", "score"}),
        ("normal", {"kind", "fqn", "name", "microservice",
                    "module", "role", "score"}),  # +file only if filename present
        ("full", {"kind", "fqn", "name", "microservice",
                  "module", "role", "score", "snippet"}),
    ):
        parsed = json.loads(render(env, fmt="json", detail=detail))
        assert set(parsed["nodes"]["com.foo.Bar"].keys()) == expected_keys, (
            f"{detail}: json key set {set(parsed['nodes']['com.foo.Bar'].keys())} != {expected_keys}"
        )
        # The text output at the same level shows the same identity label, and
        # does NOT show keys the projector dropped (snippet at brief/normal).
        text = render(env, fmt="text", noun="search", detail=detail)
        assert "Bar  @chat" in text, f"{detail}: identity label missing in text"
        if detail == "full":
            assert "void x();" in text, f"{detail}: snippet should render in full text"
        else:
            assert "void x();" not in text, f"{detail}: snippet leaked into {detail} text"


def test_listing_normal_appends_file_role_score_inline() -> None:
    """normal text appends module/role/score/file inline on the SAME line.

    Direct fix for the 'text too terse (no file/score)' complaint.
    """
    env = Envelope(
        status="ok",
        nodes={
            "sym:1": {
                "id": "sym:1", "kind": "symbol", "fqn": "com.foo.Svc.find", "name": "find",
                "microservice": "chat", "module": "core", "role": "SERVICE", "score": 0.77,
                "filename": "src/Svc.java", "start_line": 12,
            }
        },
    )
    line = render(env, fmt="text", noun="symbol", detail="normal").splitlines()[0]
    assert line.startswith("find  @chat")
    assert "module=core" in line and "role=SERVICE" in line and "score=0.77" in line
    assert "file=src/Svc.java:12" in line


def test_listing_full_appends_indented_block() -> None:
    """full text appends a per-row indented kv-block of the content fields."""
    env = Envelope(
        status="ok",
        nodes={
            "sym:1": {
                "id": "sym:1", "kind": "symbol", "fqn": "com.foo.Svc.find", "name": "find",
                "microservice": "chat", "module": "core", "role": "SERVICE",
                "signature": "find(Long)", "annotations": ["@Override"],
                "filename": "src/Svc.java", "start_line": 12,
            }
        },
    )
    out = render(env, fmt="text", noun="symbol", detail="full")
    lines = out.splitlines()
    assert lines[0].startswith("find  @chat")
    # Content fields render as an indented block under the row.
    assert "  signature: find(Long)" in lines, f"full block missing signature: {out!r}"
    assert "  annotations:" in out, f"full block missing annotations: {out!r}"


def test_edge_line_normal_appends_mechanism() -> None:
    """normal edge line appends mechanism over the brief conf-only form."""
    env = Envelope(
        status="ok",
        root="sym:0",
        nodes={
            "sym:0": {"fqn": "com.foo.Svc", "microservice": "svc"},
            "sym:1": {"fqn": "com.foo.Repo", "microservice": "svc"},
        },
        edges=[{"other_id": "sym:1", "edge_type": "INJECTS", "mechanism": "field"}],
    )
    normal = render(env, fmt="text", noun="dependencies", detail="normal")
    brief = render(env, fmt="text", noun="dependencies", detail="brief")
    assert "mechanism=field" in normal, f"normal edge missing mechanism: {normal!r}"
    assert "mechanism=" not in brief, f"brief edge leaked mechanism: {brief!r}"


def test_search_text_normal_shows_score_not_snippet() -> None:
    """Regression for the complaint: text used to drop BOTH score and snippet.

    At normal, score is now visible; the snippet stays opt-in (full only).
    Score is rounded to 3 decimals (e.g., 0.910).
    """
    out = render(_search_listing_env(), fmt="text", noun="search", detail="normal")
    assert "score=0.910" in out, f"normal search text missing score: {out!r}"
    assert "void x();" not in out, f"normal search text leaked snippet: {out!r}"


def test_search_json_normal_omits_snippet_drops_empty_fields() -> None:
    """Regression for the complaint: json used to dump the full snippet + every
    None field. At normal, snippet is gone AND symbol_id (None) is dropped."""
    parsed = json.loads(render(_search_listing_env(), fmt="json", detail="normal"))
    node = parsed["nodes"]["com.foo.Bar"]  # keyed by natural key (FQN), not chunk_id
    assert "snippet" not in node, f"normal json leaked snippet: {node!r}"
    assert "symbol_id" not in node, f"normal json kept empty symbol_id: {node!r}"
    assert node["score"] == 0.91


# ----- Traversal label disambiguation + text/json detail parity -----
#
# Regression for the `jrag callees/callers "SlaService"` complaint: the text
# rows were bare method names (getId x4, process x5, create x2) with no
# declaring class, and text/json diverged at the same --detail level (json
# carried module/role/file; text showed only `name @service conf`). Two fixes:
#   1. display_name renders method symbols as `Class#method`.
#   2. _format_edge_rows honors --detail symmetrically with _render_listing.


def test_display_name_method_includes_declaring_class() -> None:
    """A method symbol (``pkg.Class#method(args)``) renders as ``Class#method``.

    Bare method names collide across classes (getId / process / create); the
    declaring class is identity-level disambiguation and folds into the label.
    """
    from java_codebase_rag.jrag_render import display_name

    # Method FQN with carried name -> Class#name (args stripped, name preferred).
    assert display_name({
        "fqn": "com.bank.chat.contracts.InternalEvent#create(String,EventType)",
        "name": "create",
    }) == "InternalEvent#create"
    # name absent -> method name derived from the FQN tail (args stripped).
    assert display_name({"fqn": "com.foo.Repo#findById(Long)"}) == "Repo#findById"
    # Class FQN (no '#') is unchanged -> simple name.
    assert display_name({"fqn": "com.foo.SlaService", "name": "SlaService"}) == "SlaService"
    assert display_name({"fqn": "com.foo.SlaService"}) == "SlaService"


def test_display_name_does_not_double_class_when_name_carries_member_ref() -> None:
    """Regression (T7): a candidate whose ``name`` already carries the full
    ``Class#method(args)`` form must not be re-prefixed with the class.

    Ambiguous-resolve candidates populate ``name`` with the member reference
    (``JoinOperatorController#joinOperator(JoinOperatorRequest,String)``); the
    prior code prepended ``cls`` again, producing
    ``JoinOperatorController#JoinOperatorController#joinOperator(...)``. The
    verbatim name is identity-unique (class + args), so it is returned as-is.
    """
    from java_codebase_rag.jrag_render import display_name

    assert display_name({
        "fqn": "com.bank.chat.app.web.JoinOperatorController#joinOperator(JoinOperatorRequest,String)",
        "name": "JoinOperatorController#joinOperator(JoinOperatorRequest,String)",
    }) == "JoinOperatorController#joinOperator(JoinOperatorRequest,String)"
    # Traversal method nodes still carry the bare clean name -> Class#method.
    assert display_name({
        "fqn": "com.foo.Repo#findById(Long)",
        "name": "findById",
    }) == "Repo#findById"


def test_render_traversal_external_entrypoint_zero_callers_is_honest() -> None:
    """Regression (T5): a server-exposed route with zero in-repo callers must
    say so, not emit a bug-looking bare ``0 callers``.

    Text leads with ``external entrypoint — no in-repo callers``; JSON carries
    ``is_external_entrypoint: true`` (status stays ``ok``).
    """
    env = Envelope(
        status="ok",
        root="POST /chat/assign",
        nodes={"POST /chat/assign": {
            "kind": "route", "fqn": "POST /chat/assign", "microservice": "chat-assign",
        }},
        is_external_entrypoint=True,
    )

    text_out = render(env, fmt="text", noun="callers")
    assert "external entrypoint — no in-repo callers" in text_out, (
        f"external-entrypoint note missing: {text_out!r}"
    )
    assert "0 callers" not in text_out, (
        f"external entrypoint still renders bare '0 callers': {text_out!r}"
    )

    json_out = json.loads(render(env, fmt="json", noun="callers"))
    assert json_out["status"] == "ok"
    assert json_out.get("is_external_entrypoint") is True


def _traversal_env() -> Envelope:
    """root class Symbol -> one CALLS edge to a method Symbol callee.

    Carries module/role/file (normal-tier) AND signature/annotations/modifiers
    (full-tier) so the text/json parity at each level is assertable.
    """
    return Envelope(
        status="ok",
        root="sym:0",
        nodes={
            "sym:0": {
                "kind": "symbol", "fqn": "com.foo.Svc", "name": "Svc",
                "microservice": "chat", "module": "core", "role": "SERVICE",
                "symbol_kind": "class",
            },
            "sym:1": {
                "kind": "symbol",
                "fqn": "com.foo.Repo#findById(Long)",
                "name": "findById",
                "microservice": "chat", "module": "domain", "role": "REPOSITORY",
                "symbol_kind": "method",
                "signature": "findById(Long)",
                "annotations": ["@Override"],
                "modifiers": ["public"],
                "package": "com.foo",
                "filename": "src/Repo.java", "start_line": 42,
            },
        },
        edges=[{"edge_type": "CALLS", "other_id": "sym:1", "confidence": 0.88}],
    )


def test_traversal_normal_text_carries_same_fields_as_json() -> None:
    """At normal, a callees/callers text line shows the SAME node fields JSON
    shows (module/role/file), and the label is the disambiguated Class#method.

    Pre-fix the text line was ``findById @chat  conf=0.88`` only — no declaring
    class, no module, no file — while JSON carried all three.
    """
    env = _traversal_env()
    text = render(env, fmt="text", noun="callees", detail="normal")
    # Declaring class disambiguates the method target.
    assert "Repo#findById @chat" in text, f"Class#method label missing: {text!r}"
    # Inline extras match the listing normal-tier set (and JSON normal node keys).
    assert "module=domain" in text, f"module missing: {text!r}"
    assert "role=REPOSITORY" in text, f"role missing: {text!r}"
    assert "file=src/Repo.java:42" in text, f"file missing: {text!r}"
    assert "conf=0.88" in text
    # Root line is enriched the same way.
    assert "root: Svc @chat" in text and "module=core" in text, f"root not enriched: {text!r}"
    # signature/annotations stay out of normal (they are full-tier) — JSON parity.
    assert "@Override" not in text, f"annotation leaked into normal: {text!r}"
    # JSON at the same level carries exactly these keys on the callee node.
    parsed = json.loads(render(env, fmt="json", detail="normal"))
    callee = parsed["nodes"]["com.foo.Repo#findById(Long)"]
    assert {"module", "role", "file"} <= set(callee.keys()), callee
    assert "signature" not in callee, f"json normal leaked signature: {callee!r}"


def test_traversal_full_text_renders_per_edge_content_block() -> None:
    """At full, a per-edge indented block renders the callee's content fields
    (signature/annotations/modifiers/package), matching listing full and JSON
    full.

    Pre-fix ``--detail full`` was byte-identical to brief for traversals: the
    full branch walked edge attrs only (confidence, already shown), never node
    attrs, so the promised signature/annotations never appeared in text.
    """
    env = _traversal_env()
    text = render(env, fmt="text", noun="callees", detail="full")
    lines = text.splitlines()
    # Header line: disambiguated label + conf (no inline extras at full).
    assert any(ln.startswith("  Repo#findById @chat") and "conf=0.88" in ln for ln in lines), text
    # Content fields render as a block NESTED under the edge row (4-space indent).
    assert "    signature: findById(Long)" in lines, f"full block missing signature: {text!r}"
    assert "    annotations: @Override" in lines, f"full block missing annotations: {text!r}"
    assert "    modifiers: public" in lines, f"full block missing modifiers: {text!r}"
    # Root also gets a nested block at full.
    assert "  role: SERVICE" in lines, f"root block missing: {text!r}"
    # JSON full carries the same content fields on the callee node.
    parsed = json.loads(render(env, fmt="json", detail="full"))
    callee = parsed["nodes"]["com.foo.Repo#findById(Long)"]
    assert {"signature", "annotations", "modifiers", "package"} <= set(callee.keys()), callee
