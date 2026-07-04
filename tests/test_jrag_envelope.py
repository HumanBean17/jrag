"""Tests for java_codebase_rag.jrag_envelope (PR-JRAG-1a).

Pure unit tests for the envelope dataclass and the resolve-first mapper /
enum normalization / boundary helpers. The resolve_v2 path is mocked so these
tests do not require a real LadybugDB graph.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from graph_types import NodeRef
from java_codebase_rag.jrag_envelope import (
    Envelope,
    mark_truncated,
    normalize_enum,
    project_edge,
    project_envelope,
    project_node,
    resolve_query,
    to_envelope_rows,
)
from resolve_service import ResolveCandidate, ResolveOutput


# ----- Test 1: to_dict omits empty optionals -----


def test_envelope_to_dict_omits_empty_optionals() -> None:
    env = Envelope(status="ok")
    out = env.to_dict()
    # Only status remains; all optional fields omitted.
    assert out == {"status": "ok"}
    # The omitted fields:
    for key in (
        "nodes",
        "edges",
        "root",
        "candidates",
        "agent_next_actions",
        "warnings",
        "truncated",
        "file_location",
        "message",
    ):
        assert key not in out


def test_envelope_to_dict_includes_present_optionals() -> None:
    env = Envelope(
        status="ok",
        root="sym:1",
        nodes={"sym:1": {"fqn": "com.foo.Bar"}},
        warnings=["partial"],
        truncated=True,
        file_location="Bar.java:10",
    )
    out = env.to_dict()
    assert out["root"] == "sym:1"
    assert out["nodes"] == {"sym:1": {"fqn": "com.foo.Bar"}}
    assert out["warnings"] == ["partial"]
    assert out["truncated"] is True
    assert out["file_location"] == "Bar.java:10"


def test_envelope_to_json_roundtrips_status_and_message() -> None:
    import json

    env = Envelope(status="not_found", message="no match")
    out = json.loads(env.to_json())
    assert out == {"status": "not_found", "message": "no match"}


# ----- Test 2: pydantic -> dict boundary via .model_dump() -----


def test_pydantic_results_converted_via_model_dump() -> None:
    # NodeRef is a pydantic v2 BaseModel; passing one through to_envelope_rows
    # yields a plain dict (NOT a pydantic model instance).
    ref = NodeRef(id="sym:1", kind="symbol", fqn="com.foo.Bar", name="Bar")
    rows = to_envelope_rows([ref])
    assert len(rows) == 1
    assert isinstance(rows[0], dict)
    assert not hasattr(rows[0], "model_dump")
    assert rows[0]["id"] == "sym:1"
    assert rows[0]["fqn"] == "com.foo.Bar"


def test_to_envelope_rows_passes_dicts_through() -> None:
    rows = to_envelope_rows([{"id": "x"}, {"id": "y"}])
    assert rows == [{"id": "x"}, {"id": "y"}]


# ----- Tests 3-6: resolve_query -----


def _make_node(
    *,
    id: str = "sym:1",
    kind: str = "symbol",
    fqn: str = "com.foo.Bar.doStuff",
    symbol_kind: str | None = "method",
    role: str | None = "CONTROLLER",
    microservice: str | None = "foo-service",
    module: str | None = None,
) -> NodeRef:
    return NodeRef(
        id=id,
        kind=kind,  # type: ignore[arg-type]
        fqn=fqn,
        symbol_kind=symbol_kind,
        role=role,
        microservice=microservice,
        module=module,
    )


def _graph_returning_file_location(filename: str, start_line: int) -> MagicMock:
    """A mock graph whose `_rows` returns a filename/start_line row for any query."""
    g = MagicMock()
    g._rows.return_value = [{"filename": filename, "start_line": start_line}]
    return g


def test_resolve_query_one_proceeds_and_sets_file_location(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _make_node()
    fake_output = ResolveOutput(success=True, status="one", node=node, resolved_identifier="doStuff")

    def fake_resolve_v2(identifier, hint_kind=None, graph=None):
        assert identifier == "doStuff"
        return fake_output

    monkeypatch.setattr("resolve_service.resolve_v2", fake_resolve_v2)
    graph = _graph_returning_file_location("src/Foo.java", 42)
    cfg = MagicMock()
    cfg.ladybug_path = "/tmp/x/code_graph.lbug"

    result_node, env = resolve_query(
        "doStuff",
        hint_kind="symbol",
        java_kind=None,
        role=None,
        fqn_prefix=None,
        cfg=cfg,
        graph=graph,
    )

    assert result_node is not None
    assert result_node.id == "sym:1"
    assert env.status == "ok"
    assert env.root == "sym:1"
    assert env.file_location == "src/Foo.java:42"


def test_resolve_query_one_blocked_by_post_filter_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _make_node(role="SERVICE")
    fake_output = ResolveOutput(success=True, status="one", node=node)
    monkeypatch.setattr("resolve_service.resolve_v2", lambda *a, **kw: fake_output)

    graph = _graph_returning_file_location("src/Foo.java", 1)
    cfg = MagicMock()
    result_node, env = resolve_query(
        "doStuff",
        hint_kind="symbol",
        java_kind=None,
        role="CONTROLLER",  # mismatch -> post-filter fails
        fqn_prefix=None,
        cfg=cfg,
        graph=graph,
    )
    assert result_node is None
    assert env.status == "not_found"
    assert env.message is not None
    # The not_found message must surface the post-filter failure.
    assert "filters" in env.message.lower() or "post-filter" in env.message.lower()


def test_resolve_query_many_returns_candidates_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    n1 = _make_node(id="sym:1", fqn="com.foo.Bar.doStuff", microservice="foo")
    n2 = _make_node(id="sym:2", fqn="com.foo.Baz.doStuff", microservice="bar")
    fake_output = ResolveOutput(
        success=True,
        status="many",
        candidates=[
            ResolveCandidate(node=n1, score=0.9, reason="fqn_suffix"),
            ResolveCandidate(node=n2, score=0.5, reason="short_name"),
        ],
    )
    monkeypatch.setattr("resolve_service.resolve_v2", lambda *a, **kw: fake_output)
    graph = MagicMock()
    cfg = MagicMock()

    result_node, env = resolve_query(
        "doStuff",
        hint_kind="symbol",
        java_kind=None,
        role=None,
        fqn_prefix=None,
        cfg=cfg,
        graph=graph,
    )

    assert result_node is None
    assert env.status == "ambiguous"
    assert len(env.candidates) == 2
    # Each candidate carries a reason; no file or score field.
    for cand in env.candidates:
        assert "reason" in cand
        assert "file" not in cand
        assert "score" not in cand
    reasons = {c["reason"] for c in env.candidates}
    assert reasons == {"fqn_suffix", "short_name"}


def test_resolve_query_many_post_filter_collapses_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two candidates, one matching the post-filter, the other not. After
    # post-filter collapse, exactly one survives -> proceed (status=ok).
    n_match = _make_node(id="sym:1", fqn="com.foo.Bar.doStuff", microservice="foo", role="CONTROLLER")
    n_other = _make_node(id="sym:2", fqn="com.foo.Baz.doStuff", microservice="bar", role="SERVICE")
    fake_output = ResolveOutput(
        success=True,
        status="many",
        candidates=[
            ResolveCandidate(node=n_match, score=0.9, reason="fqn_suffix"),
            ResolveCandidate(node=n_other, score=0.5, reason="short_name"),
        ],
    )
    monkeypatch.setattr("resolve_service.resolve_v2", lambda *a, **kw: fake_output)
    graph = _graph_returning_file_location("Foo.java", 7)
    cfg = MagicMock()

    result_node, env = resolve_query(
        "doStuff",
        hint_kind="symbol",
        java_kind=None,
        role="controller",  # mixed-case; normalize_enum -> CONTROLLER
        fqn_prefix=None,
        cfg=cfg,
        graph=graph,
    )

    assert result_node is not None
    assert result_node.id == "sym:1"
    assert env.status == "ok"
    assert env.root == "sym:1"
    assert env.file_location == "Foo.java:7"


def test_resolve_query_many_caps_candidates_at_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    # 12 candidates, no post-filter. All 12 survive -> ambiguous, capped at 10.
    cands = [
        ResolveCandidate(
            node=_make_node(id=f"sym:{i}", fqn=f"com.foo.C{i}.doStuff", microservice="foo"),
            score=1.0 - i * 0.05,
            reason="short_name",
        )
        for i in range(12)
    ]
    fake_output = ResolveOutput(success=True, status="many", candidates=cands)
    monkeypatch.setattr("resolve_service.resolve_v2", lambda *a, **kw: fake_output)
    graph = MagicMock()
    cfg = MagicMock()

    result_node, env = resolve_query(
        "doStuff", hint_kind="symbol", java_kind=None, role=None, fqn_prefix=None, cfg=cfg, graph=graph
    )
    assert result_node is None
    assert env.status == "ambiguous"
    assert len(env.candidates) == 10  # capped


def test_resolve_query_many_post_filter_rejects_all_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (review finding A): when a post-filter rejects EVERY `many`
    candidate, the result is not_found — NOT an empty ambiguous list.

    An empty ambiguous list would render as '0 ambiguous matches' with no
    narrowing value; not_found with the filter-failure message is the honest,
    actionable result (same message as the `one` post-filter-fail branch).
    """
    n1 = _make_node(id="sym:1", fqn="com.foo.Bar.doStuff", microservice="foo", role="SERVICE")
    n2 = _make_node(id="sym:2", fqn="com.foo.Baz.doStuff", microservice="bar", role="SERVICE")
    fake_output = ResolveOutput(
        success=True,
        status="many",
        candidates=[
            ResolveCandidate(node=n1, score=0.9, reason="fqn_suffix"),
            ResolveCandidate(node=n2, score=0.5, reason="short_name"),
        ],
    )
    monkeypatch.setattr("resolve_service.resolve_v2", lambda *a, **kw: fake_output)
    graph = MagicMock()
    cfg = MagicMock()

    result_node, env = resolve_query(
        "doStuff",
        hint_kind="symbol",
        java_kind=None,
        role="CONTROLLER",  # neither candidate is CONTROLLER -> all rejected
        fqn_prefix=None,
        cfg=cfg,
        graph=graph,
    )
    assert result_node is None
    assert env.status == "not_found", (
        f"empty-many must be not_found (not {env.status!r}); candidates={env.candidates}"
    )
    assert env.candidates == []
    assert env.message is not None
    assert "filters" in env.message.lower() or "post-filter" in env.message.lower()


def test_resolve_query_none_is_not_found_with_search_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_output = ResolveOutput(
        success=True,
        status="none",
        message="No matches for identifier; use search(query=...) for ranked fuzzy lookup.",
    )
    monkeypatch.setattr("resolve_service.resolve_v2", lambda *a, **kw: fake_output)
    graph = MagicMock()
    cfg = MagicMock()

    result_node, env = resolve_query(
        "missing",
        hint_kind="symbol",
        java_kind=None,
        role=None,
        fqn_prefix=None,
        cfg=cfg,
        graph=graph,
    )
    assert result_node is None
    assert env.status == "not_found"
    assert env.message is not None
    # The CLI-specific hint must reference `jrag search` (not the MCP `search`).
    assert "jrag search" in env.message


# ----- Tests 7-9: normalize_enum -----


def test_normalize_enum_role_uppercase() -> None:
    """role/capability: case + kebab -> UPPER_SNAKE (stored uppercase).

    framework / java_kind are stored LOWERCASE (NodeFilter Literal values +
    graph node fields), so they normalize to lowercase regardless of input case
    — uppercasing them crashed `search --framework` (pydantic ValidationError)
    and made `routes --framework` return 0 results.
    """
    for input_val in ("controller", "Controller", "CONTROLLER"):
        assert normalize_enum(input_val, kind="role") == "CONTROLLER"
    # role kebab-case becomes UPPER_SNAKE.
    assert normalize_enum("rest-controller", kind="role") == "REST_CONTROLLER"

    # framework -> lowercase snake (matches NodeFilter.Framework Literal).
    assert normalize_enum("spring-mvc", kind="framework") == "spring_mvc"
    assert normalize_enum("SPRING_MVC", kind="framework") == "spring_mvc"
    assert normalize_enum("web-flux", kind="framework") == "web_flux"
    assert normalize_enum("kafka", kind="framework") == "kafka"
    # java_kind -> lowercase (matches DeclarationSymbolKind Literal).
    assert normalize_enum("class", kind="java_kind") == "class"
    assert normalize_enum("METHOD", kind="java_kind") == "method"
    assert normalize_enum("interface", kind="java_kind") == "interface"


def test_normalize_enum_client_kind_lookup() -> None:
    """client_kind: explicit lookup table -> feign_method / rest_template / web_client."""
    assert normalize_enum("feign", kind="client_kind") == "feign_method"
    assert normalize_enum("rest-template", kind="client_kind") == "rest_template"
    assert normalize_enum("rest_template", kind="client_kind") == "rest_template"
    assert normalize_enum("RestTemplate", kind="client_kind") == "rest_template"
    assert normalize_enum("web-client", kind="client_kind") == "web_client"
    assert normalize_enum("webclient", kind="client_kind") == "web_client"


def test_normalize_enum_producer_kind_lookup() -> None:
    """producer_kind: explicit lookup table -> kafka_send / stream_bridge_send."""
    assert normalize_enum("kafka", kind="producer_kind") == "kafka_send"
    assert normalize_enum("stream-bridge", kind="producer_kind") == "stream_bridge_send"
    assert normalize_enum("stream_bridge", kind="producer_kind") == "stream_bridge_send"


def test_normalize_enum_source_layer_lookup() -> None:
    """source_layer: explicit lookup table -> builtin / layer_a_meta / layer_b_* / layer_c_source."""
    assert normalize_enum("builtin", kind="source_layer") == "builtin"
    assert normalize_enum("layer-a", kind="source_layer") == "layer_a_meta"
    assert normalize_enum("layer-b-ann", kind="source_layer") == "layer_b_ann"
    assert normalize_enum("layer-b-fqn", kind="source_layer") == "layer_b_fqn"
    assert normalize_enum("layer-c", kind="source_layer") == "layer_c_source"


def test_normalize_enum_empty_passthrough() -> None:
    assert normalize_enum("", kind="role") == ""
    assert normalize_enum("   ", kind="client_kind") == ""


# ----- Test 10: mark_truncated -----


def test_mark_truncated_flags_and_clips() -> None:
    rows = list(range(8))
    visible, truncated = mark_truncated(rows, limit=5)
    assert truncated is True
    assert visible == [0, 1, 2, 3, 4]


def test_mark_truncated_no_truncation_when_under_limit() -> None:
    rows = list(range(3))
    visible, truncated = mark_truncated(rows, limit=5)
    assert truncated is False
    assert visible == [0, 1, 2]


def test_mark_truncated_boundary_equal_is_not_truncated() -> None:
    # Exactly limit rows -> not truncated (the +1 row is what signals truncation).
    rows = list(range(5))
    visible, truncated = mark_truncated(rows, limit=5)
    assert truncated is False
    assert visible == [0, 1, 2, 3, 4]


def test_mark_truncated_zero_limit() -> None:
    visible, truncated = mark_truncated([1, 2, 3], limit=0)
    assert truncated is True
    assert visible == []


def test_mark_truncated_negative_limit_raises() -> None:
    with pytest.raises(ValueError):
        mark_truncated([1, 2], limit=-1)


# ----- Tests 11-18: detail projection (PR-JRAG-6) -----
#
# `--detail brief|normal|full` is orthogonal to `--format text|json`. The
# projector is the single seam: the renderer applies it once, then both the
# JSON path and the text renderers consume the trimmed dict. These tests pin
# the field sets + the empty-field dropping + the file composition directly.


def _full_symbol_node() -> dict:
    """A node carrying the full SymbolHit-derived field set."""
    return {
        "id": "sym:1",
        "kind": "symbol",
        "fqn": "com.foo.Svc.find",
        "name": "find",
        "symbol_kind": "method",
        "microservice": "chat",
        "module": "core",
        "role": "SERVICE",
        "framework": "spring",
        "filename": "src/Svc.java",
        "start_line": 42,
        "end_line": 60,
        "signature": "find(Long)",
        "annotations": ["@Override"],
        "capabilities": ["TX"],
        "modifiers": ["public"],
        "package": "com.foo",
        "parent_id": "sym:0",
        "resolved": True,
        "score": 0.91,
    }


def test_project_node_brief_keeps_identity_drops_extras() -> None:
    """brief == today's terse identity set; location/ranking/content dropped."""
    out = project_node(_full_symbol_node(), "brief")
    # Identity keys survive.
    for key in ("id", "kind", "fqn", "name", "microservice", "resolved"):
        assert key in out, f"brief dropped identity key {key!r}"
    # file/score (ranking/location) and content fields are dropped.
    for key in ("module", "role", "symbol_kind", "file", "score", "signature",
                "annotations", "capabilities", "package", "parent_id"):
        assert key not in out, f"brief leaked {key!r}"
    # Raw location columns are folded away (no filename/start_line at any level).
    assert "filename" not in out and "start_line" not in out


def test_project_node_normal_adds_location_and_ranking() -> None:
    """normal adds module/role/symbol_kind/framework/file/score over brief.

    This is the fix for the 'text too terse' complaint: file + score become
    visible. Content fields (signature/annotations/...) still dropped.
    """
    out = project_node(_full_symbol_node(), "normal")
    for key in ("id", "kind", "fqn", "name", "microservice",
                "module", "role", "symbol_kind", "framework", "score", "resolved"):
        assert key in out, f"normal dropped {key!r}"
    # file is composed from filename+start_line.
    assert out["file"] == "src/Svc.java:42"
    # Content still suppressed at normal.
    for key in ("signature", "annotations", "capabilities", "modifiers", "package", "parent_id"):
        assert key not in out, f"normal leaked content {key!r}"


def test_project_node_full_keeps_everything() -> None:
    """full keeps every present key (still composes file + drops empties)."""
    out = project_node(_full_symbol_node(), "full")
    for key in ("signature", "annotations", "capabilities", "modifiers",
                "package", "parent_id", "score", "file", "role", "module"):
        assert key in out, f"full dropped {key!r}"
    assert out["file"] == "src/Svc.java:42"
    # Raw location columns are folded into `file` even at full.
    assert "filename" not in out and "start_line" not in out and "end_line" not in out


def test_project_node_drops_empty_fields_at_all_levels() -> None:
    """None / '' / [] / {} vanish at every level (the '10 empty fields' fix).

    A SearchHit dump used to serialize ``symbol_id: null, role: null, module: null``.
    The projector drops them. ``False`` and ``0.0`` are NOT empty (meaningful).
    """
    node = {
        "id": "chunk:1",
        "kind": "search_hit",
        "fqn": "com.foo.Bar",
        "name": "Bar",
        "microservice": "chat",
        "score": 0.0,          # NOT empty
        "snippet": "body",     # only at full
        "module": None,        # empty
        "role": "",            # empty
        "symbol_id": None,     # empty
        "capabilities": [],    # empty
        "resolved": False,     # NOT empty (meaningful)
    }
    for detail in ("brief", "normal", "full"):
        out = project_node(node, detail)
        # Empty values dropped at every level.
        assert "module" not in out and "role" not in out, f"{detail}: empty kept"
        assert "symbol_id" not in out and "capabilities" not in out, f"{detail}: empty kept"
        # 0.0 / False are NOT empty (meaningful) — survive when in the level's set.
        # `resolved` is identity (in brief); `score` is normal/full only.
        assert out.get("resolved") is False, f"{detail}: False resolved wrongly dropped"
        if detail in ("normal", "full"):
            assert out.get("score") == 0.0, f"{detail}: 0.0 score wrongly dropped"
        else:
            assert "score" not in out, f"{detail}: score is not a brief field"


def test_compose_file_from_filename_and_start_line() -> None:
    """file = 'filename:start_line'; bare filename when no line; absent when no filename."""
    assert project_node({"id": "1", "kind": "symbol", "fqn": "x", "name": "x",
                         "filename": "A.java", "start_line": 7}, "normal")["file"] == "A.java:7"
    assert project_node({"id": "1", "kind": "symbol", "fqn": "x", "name": "x",
                         "filename": "A.java"}, "normal")["file"] == "A.java"
    out = project_node({"id": "1", "kind": "symbol", "fqn": "x", "name": "x"}, "normal")
    assert "file" not in out


def test_project_envelope_passes_through_envelope_level_fields() -> None:
    """status/root/warnings/truncated/file_location/message/agent_next_actions
    are envelope-level — projected through unchanged (no detail axis on them)."""
    env = Envelope(
        status="ok",
        nodes={"sym:1": _full_symbol_node()},
        root="sym:1",
        warnings=["w1"],
        truncated=True,
        file_location="src/Svc.java:42",
        message=None,
    )
    env.agent_next_actions = ["jrag inspect Svc"]
    p = project_envelope(env, "brief")
    assert p.status == "ok"
    assert p.root == "sym:1"
    assert p.warnings == ["w1"]
    assert p.truncated is True
    assert p.file_location == "src/Svc.java:42"
    assert p.agent_next_actions == ["jrag inspect Svc"]
    # Nodes ARE projected (brief drops the content).
    assert "signature" not in p.nodes["sym:1"]


def test_project_edge_brief_normal_full_attr_sets() -> None:
    edge = {
        "other_id": "sym:2",
        "edge_type": "INJECTS",
        "confidence": 0.5,
        "mechanism": "field",
        "annotation": "@Inject",
        "field_or_param": "repo",
        "from_fqn": "com.foo.Svc",
        "role": "REPOSITORY",
    }
    brief = project_edge(edge, "brief")
    assert "other_id" in brief and "edge_type" in brief
    assert "mechanism" not in brief and "annotation" not in brief
    normal = project_edge(edge, "normal")
    assert normal.get("mechanism") == "field"
    assert "annotation" not in normal and "field_or_param" not in normal
    full = project_edge(edge, "full")
    for key in ("mechanism", "annotation", "field_or_param", "from_fqn", "role"):
        assert key in full, f"full edge dropped {key!r}"


def test_project_envelope_bad_detail_raises() -> None:
    """A typo must raise, not silently behave like full."""
    env = Envelope(status="ok", nodes={"sym:1": {"id": "1", "kind": "symbol", "fqn": "x"}})
    with pytest.raises(ValueError):
        project_envelope(env, "bogus")
