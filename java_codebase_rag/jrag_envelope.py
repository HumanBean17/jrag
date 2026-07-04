"""JRAG envelope dataclass + resolve-first mapper + enum normalization (PR-JRAG-1a).

This is the frozen contract every later JRAG-CLI PR builds on. The envelope is a
lean ``@dataclass`` (not pydantic): backend pydantic outputs cross the boundary
via ``.model_dump()`` exactly once in :func:`to_envelope_rows`. Renderers and
``to_json()`` operate on plain dicts only.

Lazy imports: :mod:`resolve_service` and :mod:`ladybug_queries` are imported
inside :func:`resolve_query` so this module's import stays light (no torch, no
sentence_transformers, no mcp_v2). The dataclass and pure helpers
(``normalize_enum``/``mark_truncated``/``simple_name``/``to_envelope_rows``) do
not need any backend module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from graph_types import NodeRef

__all__ = [
    "Envelope",
    "EnvelopeStatus",
    "resolve_query",
    "normalize_enum",
    "mark_truncated",
    "simple_name",
    "to_envelope_rows",
]


EnvelopeStatus = Literal["ok", "ambiguous", "not_found", "error"]

# Explicit lookup tables for kinds whose stored literal is not a plain
# UPPER_SNAKE form of the user's input. Confirmed against java_ontology.py and
# graph_enrich.py source:
#  - client_kind literals: feign_method / rest_template / web_client
#    (java_ontology.VALID_CLIENT_KINDS)
#  - producer_kind literals: kafka_send / stream_bridge_send
#    (java_ontology.VALID_PRODUCER_KINDS)
#  - source_layer literals: builtin / layer_a_meta / layer_b_ann /
#    layer_b_fqn / layer_c_source (graph_enrich.route_source_layer assignments)
#
# Keys are the *normalized* form (lowercase + kebab/space -> underscore).
_CLIENT_KIND_TABLE: dict[str, str] = {
    "feign": "feign_method",
    "feign_method": "feign_method",
    "rest_template": "rest_template",
    "resttemplate": "rest_template",
    "web_client": "web_client",
    "webclient": "web_client",
}

_PRODUCER_KIND_TABLE: dict[str, str] = {
    "kafka": "kafka_send",
    "kafka_send": "kafka_send",
    "stream_bridge": "stream_bridge_send",
    "stream_bridge_send": "stream_bridge_send",
    "streambridge": "stream_bridge_send",
}

_SOURCE_LAYER_TABLE: dict[str, str] = {
    "builtin": "builtin",
    "layer_a": "layer_a_meta",
    "layer_a_meta": "layer_a_meta",
    "layer_b_ann": "layer_b_ann",
    "layer_b_fqn": "layer_b_fqn",
    "layer_c": "layer_c_source",
    "layer_c_source": "layer_c_source",
}

_ENUM_LOOKUP_TABLES: dict[str, dict[str, str]] = {
    "client_kind": _CLIENT_KIND_TABLE,
    "producer_kind": _PRODUCER_KIND_TABLE,
    "source_layer": _SOURCE_LAYER_TABLE,
}


@dataclass
class Envelope:
    """The single output shape every jrag command emits.

    Backend pydantic outputs are converted to plain dicts at the boundary
    (``to_envelope_rows``); the renderer and ``to_json()`` operate on dicts
    only. ``to_dict()`` omits empty optionals so a clean status=ok envelope
    stays small.
    """

    status: EnvelopeStatus
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    root: str | None = None
    candidates: list[dict] = field(default_factory=list)
    agent_next_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    file_location: str | None = None
    # Used to carry the resolve ``message`` for not_found / error envelopes
    # (the renderer surfaces it as ``not found: <message>``). None on ok/ambiguous.
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-ready dict, omitting empty optionals.

        Top-level collection fields are shallow-copied (``list(...)`` /
        ``dict(...)``); their VALUES are shared references - mutating a node
        dict in place will propagate to a prior snapshot. Callers that need
        true snapshot isolation across subsequent mutation should
        ``copy.deepcopy`` the result. (In practice the envelope is short-lived:
        built, rendered via ``to_json()`` in the same call site, then discarded
        - so shared references are not a hazard.)
        """
        out: dict[str, Any] = {"status": self.status}
        if self.nodes:
            out["nodes"] = dict(self.nodes)
        if self.edges:
            out["edges"] = list(self.edges)
        if self.root is not None:
            out["root"] = self.root
        if self.candidates:
            out["candidates"] = list(self.candidates)
        if self.agent_next_actions:
            out["agent_next_actions"] = list(self.agent_next_actions)
        if self.warnings:
            out["warnings"] = list(self.warnings)
        if self.truncated:
            out["truncated"] = True
        if self.file_location is not None:
            out["file_location"] = self.file_location
        if self.message is not None:
            out["message"] = self.message
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def simple_name(node_dict: dict[str, Any]) -> str:
    """Simple name = ``fqn.rsplit('.', 1)[-1]``.

    ``NodeRef`` carries no ``name`` field; the rendering layer derives a short
    label from the FQN on demand. Empty/missing FQN returns "".
    """
    fqn = str(node_dict.get("fqn") or "")
    if not fqn:
        return ""
    return fqn.rsplit(".", 1)[-1]


def to_envelope_rows(pydantic_results: list[Any]) -> list[dict[str, Any]]:
    """Pydantic -> dict boundary: ``.model_dump()`` each item exactly once.

    Accepts pydantic models (``.model_dump()``) or plain dicts (passthrough).
    Any other type raises ``TypeError`` rather than silently coercing - the
    boundary is a single-shape conversion, not a best-effort adapter, and a
    non-dict/non-pydantic item signals a backend-contract bug we want to
    surface immediately.
    """
    out: list[dict[str, Any]] = []
    for item in pydantic_results:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump())
        elif isinstance(item, dict):
            out.append(item)
        else:
            raise TypeError(
                f"to_envelope_rows: expected pydantic model or dict, got {type(item).__name__}"
            )
    return out


def mark_truncated(rows: list[Any], limit: int) -> tuple[list[Any], bool]:
    """+1-fetch trick.

    Pass ``limit+1`` to the backend; this helper drops the overflow row and
    reports whether truncation occurred. ``limit`` must be ``>= 0``.
    """
    if limit < 0:
        raise ValueError(f"mark_truncated: limit must be >= 0, got {limit}")
    truncated = len(rows) > limit
    if not truncated:
        return list(rows), False
    return list(rows[:limit]), True


def normalize_enum(value: str, *, kind: str) -> str:
    """Normalize a user-supplied enum to the graph's stored literal form.

    * role / capability / framework / java_kind: case + kebab -> UPPER_SNAKE
      (the stored literals are uppercase; e.g. ``Controller``/``controller``
      -> ``CONTROLLER``, ``web-flux`` -> ``WEB_FLUX``).
    * client_kind / producer_kind / source_layer: routed through the explicit
      lookup tables above (the stored literals are lowercase_snake with
      non-obvious suffixes: ``feign`` -> ``feign_method``, ``kafka`` ->
      ``kafka_send``, ``layer-a`` -> ``layer_a_meta``).

    Empty input returns empty. Unknown lookup values fall through to the
    UPPER_SNAKE path so callers see *something* (validation against the
    graph's ``VALID_*`` set happens at the command layer).
    """
    raw = (value or "").strip()
    if not raw:
        return raw
    table = _ENUM_LOOKUP_TABLES.get(kind)
    if table is not None:
        if raw in table:
            return table[raw]
        norm = raw.lower().replace("-", "_").replace(" ", "_")
        if norm in table:
            return table[norm]
        # Fall through to UPPER_SNAKE for unknown values; the command layer
        # validates against VALID_CLIENT_KINDS / VALID_PRODUCER_KINDS / the
        # source_layer set and emits an actionable error envelope.
    return raw.upper().replace("-", "_").replace(" ", "_")


def _matches_post_filters(
    node: NodeRef,
    *,
    java_kind: str | None,
    role: str | None,
    fqn_prefix: str | None,
) -> bool:
    """Client-side post-filter on a resolved node (PR-JRAG-1a resolve-first)."""
    if java_kind is not None:
        want = normalize_enum(java_kind, kind="java_kind")
        actual = (node.symbol_kind or "").upper().replace("-", "_")
        if actual != want:
            return False
    if role is not None:
        want = normalize_enum(role, kind="role")
        actual = (node.role or "").upper().replace("-", "_")
        if actual != want:
            return False
    if fqn_prefix is not None:
        if not (node.fqn or "").startswith(fqn_prefix):
            return False
    return True


def _candidate_to_dict(node: NodeRef, reason: str) -> dict[str, Any]:
    """Build a candidate dict for the ambiguous envelope, carrying ``reason``.

    No ``file`` / ``score`` fields — ambiguous candidates are not file pointers
    or ranked matches, they are *narrowing* hints (PR-JRAG-1a renderer spec).
    """
    return {
        "id": node.id,
        "fqn": node.fqn,
        "kind": node.kind,
        "name": simple_name({"fqn": node.fqn}),
        "microservice": node.microservice,
        "module": node.module,
        "role": node.role,
        "symbol_kind": node.symbol_kind,
        "reason": reason,
    }


def _node_file_location(graph: Any, node_id: str) -> str | None:
    """Fetch ``filename:start_line`` for a resolved node from the graph.

    ``NodeRef`` does not carry ``filename`` / ``start_line`` (graph_types.NodeRef
    only has id/kind/fqn/symbol_kind/microservice/module/role); the resolved
    node's location is fetched separately via a single-column Cypher lookup.
    """
    rows = graph._rows(  # noqa: SLF001 - same pattern as mcp_v2._load_node_record
        "MATCH (n) WHERE n.id = $id "
        "RETURN n.filename AS filename, n.start_line AS start_line LIMIT 1",
        {"id": node_id},
    )
    if not rows:
        return None
    row = rows[0]
    filename = str(row.get("filename") or "").strip()
    if not filename:
        return None
    start_line = row.get("start_line")
    if start_line:
        try:
            return f"{filename}:{int(start_line)}"
        except (TypeError, ValueError):
            return filename
    return filename


def resolve_query(
    identifier: str,
    *,
    hint_kind: Literal["symbol", "route", "client", "producer"] | None,
    java_kind: str | None,
    role: str | None,
    fqn_prefix: str | None,
    cfg: Any,
    graph: Any | None = None,
) -> tuple[NodeRef | None, Envelope]:
    """Resolve-first mapper: runs ``resolve_v2`` and maps its contract to the envelope.

    * ``one`` -> apply post-filters (``java_kind`` / ``role`` / ``fqn_prefix``)
      to the resolved node. If pass: ``(node, env ok)`` with
      ``env.file_location`` set from the node's ``filename`` + ``start_line``
      and ``env.root = node.id``. If fail: ``(None, env not_found)``.
    * ``many`` -> apply post-filters to candidates. If exactly one survives,
      treat as ``one`` (proceed). Else ``(None, env ambiguous)`` with candidates
      capped at 10, each carrying ``reason``. Auto-pick is forbidden.
    * ``none`` -> ``(None, env not_found)`` with a message mentioning
      ``jrag search``.

    ``cfg`` is a ``ResolvedOperatorConfig`` (typed loosely to keep this module
    cocoindex-free and to avoid importing the operator config layer here).
    ``graph`` is optional for testability; in production the caller passes the
    graph it loaded via :func:`jrag._load_graph`.
    """
    # Lazy imports — keeps build_parser() / `jrag --help` free of resolve/ladybug.
    from resolve_service import resolve_v2

    if graph is None:
        from ladybug_queries import LadybugGraph

        graph = LadybugGraph.get(str(cfg.ladybug_path))

    out = resolve_v2(identifier, hint_kind=hint_kind, graph=graph)

    if out.status == "one" and out.node is not None:
        node = out.node
        if _matches_post_filters(node, java_kind=java_kind, role=role, fqn_prefix=fqn_prefix):
            env = Envelope(status="ok", root=node.id)
            loc = _node_file_location(graph, node.id)
            if loc is not None:
                env.file_location = loc
            return node, env
        return None, Envelope(
            status="not_found",
            message=(
                f"No matches for {identifier!r} after applying --java-kind/--role/--fqn-prefix "
                "filters; use `jrag search <query>` for ranked fuzzy lookup."
            ),
        )

    if out.status == "many" and out.candidates:
        survivors = [
            c for c in out.candidates
            if _matches_post_filters(c.node, java_kind=java_kind, role=role, fqn_prefix=fqn_prefix)
        ]
        if len(survivors) == 1:
            node = survivors[0].node
            env = Envelope(status="ok", root=node.id)
            loc = _node_file_location(graph, node.id)
            if loc is not None:
                env.file_location = loc
            return node, env
        capped = survivors[:10]
        env = Envelope(
            status="ambiguous",
            candidates=[_candidate_to_dict(c.node, c.reason) for c in capped],
        )
        return None, env

    # status == "none" (or "one"/"many" with missing data — treat as not_found).
    raw_msg = out.message or f"No matches for {identifier!r}."
    # Always surface the CLI-specific `jrag search` hint (resolve_v2's built-in
    # message references the MCP `search(query=...)` form, which is wrong for
    # the agent-facing CLI).
    if "jrag search" not in raw_msg:
        raw_msg = f"{raw_msg} Use `jrag search <query>` for ranked fuzzy lookup."
    return None, Envelope(status="not_found", message=raw_msg)
