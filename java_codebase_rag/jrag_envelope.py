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
import re
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
    "next_actions_hook",
    "project_node",
    "project_edge",
    "project_envelope",
    "node_key",
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

    Internal vs agent-facing: ``nodes`` is keyed by the graph node id internally
    (handlers build ``nodes[h.id] = ...``), and ``to_dict()`` preserves that
    id-keyed shape for debugging / internal use. ``to_json()`` — the CLI output
    boundary — is id-free: it re-keys ``nodes`` to each node's natural key
    (FQN / path / topic / literal), strips graph-id fields (``id`` / ``*_id``),
    and collapses edge id-refs into ``target``. The CLI is resolve-first, so no
    raw graph id ever reaches an agent on either the text or the JSON surface.
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
    # Set when a traversal root is a server-exposed entrypoint (an HTTP route
    # with an inbound EXPOSES edge from a controller Symbol) that genuinely has
    # zero in-repo callers. Distinguishes the *correct* empty result ("external
    # entrypoint — no in-repo callers") from a bug-looking bare "0 callers".
    is_external_entrypoint: bool = False

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
        if self.is_external_entrypoint:
            out["is_external_entrypoint"] = True
        return out

    def to_json(self) -> str:
        """Serialize to the AGENT-FACING id-free JSON string.

        This is the CLI output boundary: it does NOT delegate to
        :meth:`to_dict` (which stays id-keyed for internal/debug use and is
        unit-tested as such). Instead it builds a fresh, id-free dict:

          * ``nodes`` is re-keyed from raw graph ids to each node's natural key
            via :func:`node_key`; when ``node_key`` returns ``None`` (no
            identity field — e.g. status/orientation rollup nodes) the existing
            dict key is kept unchanged. Each node value is stripped of graph-id
            fields (``id`` / ``*_id``).
          * each edge's ``other_id`` / ``dst_id`` / ``target_id`` / ``term_id``
            (only ``other_id`` is ever emitted by handlers) collapses to a
            single ``"target"`` holding the referenced node's natural key. A
            dangling ref (no matching node) keeps its literal value — never
            null, never silently dropped.
          * ``root`` becomes the root node's natural key (omitted when absent).
          * ``candidates`` are stripped of graph-id fields.
          * Envelope-level scalars (``status`` / ``warnings`` / ``truncated`` /
            ``file_location`` / ``message`` / ``agent_next_actions``) pass
            through unchanged.

        Builds NEW dicts throughout (no in-place mutation of ``self``).
        """
        return json.dumps(self._to_idfree_dict())

    def _to_idfree_dict(self) -> dict[str, Any]:
        """Build the id-free agent-facing dict (see :meth:`to_json`)."""
        # 1. id -> natural key map (falls back to the existing key when node_key
        #    returns None, preserving literal keys like "index"/"microservices").
        id_to_key: dict[str, str] = {}
        used: set[str] = set()
        for id_key, node in self.nodes.items():
            natural = node_key(node)
            if natural is None:
                # No semantic identity. Keep the existing dict key ONLY when it
                # is not a raw graph id (e.g. the literal "index" / "microservices"
                # rollup keys). If it IS a raw id (40-hex SHA or a prefixed hash
                # form like r:phantom:<hex> / ucs:<hex>), synthesize an opaque
                # positional key so no graph id ever leaks as a JSON key.
                if _looks_like_raw_graph_id(id_key):
                    key = f"node-{len(used)}"
                else:
                    key = id_key
            else:
                key = natural
                # Collision suffix: first occurrence unsuffixed, then #2, #3, ...
                if key in used:
                    base = key
                    n = 2
                    while key in used:
                        key = f"{base}#{n}"
                        n += 1
            used.add(key)
            id_to_key[id_key] = key

        out: dict[str, Any] = {"status": self.status}

        if self.nodes:
            out["nodes"] = {
                id_to_key[nid]: _strip_graph_id_fields(dict(node))
                for nid, node in self.nodes.items()
            }
        if self.edges:
            out["edges"] = [self._edge_to_idfree(e, id_to_key) for e in self.edges]
        if self.root is not None:
            # The root's natural key (falls back to the raw root id only if the
            # root node isn't in self.nodes — a defensive no-op in practice).
            out["root"] = id_to_key.get(self.root, self.root)
        if self.candidates:
            out["candidates"] = [_strip_graph_id_fields(dict(c)) for c in self.candidates]
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
        if self.is_external_entrypoint:
            out["is_external_entrypoint"] = True
        return out

    @staticmethod
    def _edge_to_idfree(edge: dict[str, Any], id_to_key: dict[str, str]) -> dict[str, Any]:
        """Copy an edge, collapsing id-ref variants into one ``target`` key.

        Mirrors :func:`jrag_render._node_id`'s variant list. Only ``other_id``
        is emitted by handlers in practice; the others are defensive. The
        remaining raw id-ref keys are dropped; all other edge attrs pass through.
        """
        out: dict[str, Any] = {}
        ref_value: str | None = None
        for k, v in edge.items():
            if k in ("other_id", "dst_id", "target_id", "term_id"):
                if ref_value is None and isinstance(v, str) and v:
                    ref_value = v
                # skip (don't copy the raw id-ref key)
            elif not _is_graph_id_field(k):
                out[k] = v
        if ref_value is not None:
            out["target"] = id_to_key.get(ref_value, ref_value)
        return out


def simple_name(node_dict: dict[str, Any]) -> str:
    """Simple name = ``fqn.rsplit('.', 1)[-1]``.

    ``NodeRef`` carries no ``name`` field; the rendering layer derives a short
    label from the FQN on demand. Empty/missing FQN returns "".
    """
    fqn = str(node_dict.get("fqn") or "")
    if not fqn:
        return ""
    return fqn.rsplit(".", 1)[-1]


def node_key(node: dict[str, Any]) -> str | None:
    """Derive a stable, agent-meaningful, NON-graph-id key for a node.

    Used by :meth:`Envelope.to_json` to re-key ``nodes`` (away from raw graph
    ids) and to translate edge ``other_id`` refs. Returns ``None`` when no
    identity field is derivable, in which case :meth:`Envelope.to_json` keeps
    the existing dict key unchanged (this preserves already-id-free literal
    keys such as ``"index"`` / ``"microservices"`` / ``"map"`` / ``"conventions"``
    that status / orientation commands build).

    Precedence (first non-empty wins):
      * ``fqn``           -> symbols AND route roots. Route roots come from
                             ``NodeRef.model_dump()`` whose ``fqn`` already
                             carries ``"METHOD path"`` (no separate path/method
                             fields), so this single branch covers them.
      * ``member_fqn``    -> clients: ``member_fqn->target_service`` (disambiguates
                             a client member from a symbol of the same name).
      * ``topic``         -> producers/topics: ``topic:<name>`` (the ``topic:``
                             prefix matches the existing _cmd_topics key shape).
      * ``name``          -> fallback for any other named node.
      * ``file``          -> unresolved/phantom routes carry no fqn/path/topic/name
                             but DO carry a composed ``file`` location; keying by
                             it avoids leaking the raw graph id (e.g.
                             ``r:phantom:<hash>``) when no semantic id exists.
      * else              -> ``None`` (caller keeps the existing dict key — safe
                             only when that key is already a non-id literal, e.g.
                             the ``"index"`` / ``"microservices"`` rollup keys).
    """
    fqn = str(node.get("fqn") or "").strip()
    if fqn:
        return fqn
    member_fqn = str(node.get("member_fqn") or "").strip()
    if member_fqn:
        target = str(node.get("target_service") or "").strip()
        return f"{member_fqn}->{target}" if target else member_fqn
    topic = str(node.get("topic") or "").strip()
    if topic:
        return f"topic:{topic}"
    name = str(node.get("name") or "").strip()
    if name:
        return name
    file_loc = str(node.get("file") or "").strip()
    if file_loc:
        return file_loc
    return None


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
    # framework / java_kind (symbol_kind) literals are stored LOWERCASE — both
    # in the graph (Route.framework, Symbol.kind) and in the NodeFilter Literal
    # types (mcp_v2.Framework / DeclarationSymbolKind). Uppercasing them broke
    # `routes --framework`, `find --java-kind` filter mode, and crashed
    # `search --framework` with a pydantic ValidationError. role / capability
    # stay UPPER_SNAKE (those ARE stored uppercase).
    if kind in ("framework", "java_kind"):
        return raw.lower().replace("-", "_").replace(" ", "_")
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
        # symbol_kind is stored LOWERCASE (DeclarationSymbolKind: class/method/...);
        # normalize_enum now returns lowercase for java_kind, so compare on the
        # lowercased actual (was upper-vs-upper, which only worked by accident).
        actual = (node.symbol_kind or "").lower().replace("-", "_")
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


def _constructor_owner_fqn(node: NodeRef) -> str | None:
    """If ``node`` is a constructor, return its owning class FQN; else None.

    A constructor's FQN is ``<classFqn>#<simpleName>(args)`` where the member
    name equals the class's simple name (``com.x.Foo#Foo(...)``). ``symbol_kind``
    may be ``"constructor"`` or, on older nodes, ``"method"`` — the FQN shape is
    authoritative. Used by the class-vs-constructor auto-pick in
    :func:`resolve_query` so ``inspect/callers/callees <ClassName>`` does not
    bounce to "ambiguous" just because the class shares its name with its ctor.
    """
    fqn = (node.fqn or "").strip()
    if "#" not in fqn:
        return None
    head, rest = fqn.split("#", 1)
    member = rest.split("(", 1)[0].strip()
    class_simple = head.rsplit(".", 1)[-1]
    if member and member == class_simple:
        return head
    return None


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
        if not survivors:
            # Every `many` candidate was rejected by the post-filters — there is
            # nothing left to disambiguate, so this is not_found, NOT an empty
            # ambiguous list (which would render as "0 ambiguous matches" with no
            # narrowing value). Same message as the `one` post-filter-fail branch.
            return None, Envelope(
                status="not_found",
                message=(
                    f"No matches for {identifier!r} after applying --java-kind/--role/--fqn-prefix "
                    "filters; use `jrag search <query>` for ranked fuzzy lookup."
                ),
            )
        # Class-vs-constructor auto-pick: a class and its constructor share a
        # simple name, so resolve_v2 returns "many" for ANY
        # `inspect/callers/callees/decompose/dependencies <ClassName>`. When the
        # survivors are exactly ONE type (FQN with no '#') plus one-or-more
        # constructors OF THAT SAME TYPE, auto-pick the type. The constructor
        # stays reachable via its explicit FQN or `--java-kind constructor`.
        # Two genuinely-different types (same simple name across services) still
        # surface as ambiguous — we never silently guess across distinct classes.
        if len(survivors) >= 2:
            type_survivors = [c for c in survivors if "#" not in (c.node.fqn or "")]
            member_survivors = [c for c in survivors if "#" in (c.node.fqn or "")]
            if len(type_survivors) == 1 and member_survivors:
                type_fqn = (type_survivors[0].node.fqn or "").strip()
                if all(_constructor_owner_fqn(c.node) == type_fqn for c in member_survivors):
                    node = type_survivors[0].node
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


def next_actions_hook(
    envelope: Envelope,
    root: str | None = None,
    edge_summary: dict[str, Any] | None = None,
    result_edges: list[dict[str, Any]] | None = None,
    command: str | None = None,
) -> list[str]:
    """Populate ``envelope.agent_next_actions`` via :mod:`jrag_hints` (PR-JRAG-4).

    Every command that produces edges or an edge_summary calls this hook. The
    hook extracts the root node's FQN from ``envelope.nodes[root]`` and delegates
    to :func:`jrag_hints.next_actions`, which maps edge labels → ``jrag <cmd>
    <fqn>`` hints (≤5, zero-direction suppressed, dot-keys covered). The result
    is assigned to ``envelope.agent_next_actions`` (auto-omitted from
    ``to_dict()`` when empty — see :meth:`Envelope.to_dict`).

    Skipped (returns ``[]``) when:
      * ``root`` is ``None`` (listing / find / outline commands — no single root).
      * The root node is absent from ``envelope.nodes`` (defensive).
      * The root node's ``fqn`` is empty/missing.
      * The root node is a synthetic kind (``microservice`` / ``topic`` /
        ``unresolved_import``) — hints targeting a synthetic id would never
        resolve and would mislead the agent.

    Args:
        envelope: The output envelope (mutated in place: ``agent_next_actions``
            is set on success).
        root: The root node id (for commands that resolve a single node).
        edge_summary: The edge_summary from describe_v2 (inspect command only).
        result_edges: Raw edge rows from traversal commands (used when
            ``edge_summary`` is ``None``).

    Returns:
        The list of hint strings assigned to ``envelope.agent_next_actions``
        (empty when the hook was a no-op for this call).
    """
    if root is None:
        return []
    root_node = envelope.nodes.get(root)
    if root_node is None:
        return []
    root_fqn = str(root_node.get("fqn") or "").strip()
    if not root_fqn:
        return []
    # Suppress hints for synthetic roots (microservice connection view, topic
    # grouping, unresolved imports) — these would produce ``jrag callees <name>``
    # style hints that would never resolve.
    kind = str(root_node.get("kind") or "")
    if kind in ("microservice", "topic", "unresolved_import"):
        return []
    from java_codebase_rag.jrag_hints import next_actions

    envelope.agent_next_actions = next_actions(
        root_fqn=root_fqn,
        edge_summary=edge_summary,
        result_edges=result_edges if result_edges is not None else list(envelope.edges),
        current_command=command,
    )
    return envelope.agent_next_actions


# ---------------------------------------------------------------------------
# Output detail projection (PR-JRAG-6).
#
# ``--detail brief|normal|full`` is ORTHOGONAL to ``--format text|json``. The
# renderer calls :func:`project_envelope` once, then BOTH the JSON path and the
# text renderers consume the trimmed dict — so ``--format json --detail brief``
# and ``--format text --detail brief`` go through the SAME field set.
#
# Detail was previously decided per-handler at node-dict construction
# (``_symbol_hit_to_dict`` trimmed; ``SearchHit.model_dump()`` carried the full
# snippet), which coupled "how much" to "which format" and made JSON dump 50-
# line snippets + 10 empty fields while text showed only ``Name @service``.
# Inverting to "carry full, trim at one seam" makes the two axes independent.
#
# Key-sets are CATEGORY-based (intersected with each node's present keys), so
# they are kind-agnostic and auto-handle new node kinds: a route at ``normal``
# shows the same categories of fields as a symbol at ``normal``.
# ---------------------------------------------------------------------------

# Raw location columns carried by SymbolHit; folded into the display field
# ``file`` by :func:`_compose_file`. They are NOT display fields themselves.
_RAW_LOCATION_KEYS = frozenset(
    {"filename", "start_line", "end_line", "start_byte", "end_byte"}
)

# Identity only == the keys the text renderers' display_name / tiered_name read.
# Reproduces today's terse text output exactly at ``brief``. ``reason`` is
# candidate-structural (the ambiguous narrowing hint), so it survives at every
# level — a candidate without its reason is useless.
#
# NOTE: ``id`` is intentionally ABSENT. Graph node ids (40-hex SHAs) are an
# internal join key, never an agent-facing identifier — the CLI is resolve-first
# (agents pass FQN / simple name / route / topic). :func:`project_node` drops
# ``id`` and every ``*_id`` graph foreign key at every detail level via
# :func:`_is_graph_id_field`; see the boundary-strip rule there.
_BRIEF_NODE_KEYS: frozenset[str] = frozenset(
    {
        "kind",
        "fqn",
        "name",
        "microservice",
        "path",
        "method",
        "topic",
        "member_fqn",
        "target_service",
        "broker",
        "client_kind",
        "producer_kind",
        "import_simple",
        "import_fqn",
        "import_kind",
        "resolved",
        "reason",
    }
)

# brief + location / classification / ranking. ``file`` is the composed
# ``filename:start_line`` display field (see :func:`_compose_file`).
_NORMAL_NODE_KEYS: frozenset[str] = _BRIEF_NODE_KEYS | frozenset(
    {"module", "role", "symbol_kind", "framework", "file", "score"}
)

# Edge attrs the text renderers read at the default level (target id variants
# across backends + the grouping/confidence keys).
_BRIEF_EDGE_KEYS: frozenset[str] = frozenset(
    {
        "other_id",
        "dst_id",
        "target_id",
        "term_id",
        "edge_type",
        "stored_edge_type",
        "label",
        "type",
        "confidence",
        "direction",
        "section",
        "stage",
        "resolved",
    }
)

# brief + the cheap edge attrs (injection mechanism, role label, origin fqn).
_NORMAL_EDGE_KEYS: frozenset[str] = _BRIEF_EDGE_KEYS | frozenset(
    {"mechanism", "role", "from_fqn"}
)


def _is_empty(value: Any) -> bool:
    """True for values that carry no information: ``None`` / ``""`` / ``[]`` / ``{}``.

    ``False`` and ``0`` / ``0.0`` are NOT empty (they are meaningful: an
    unresolved ``resolved=False`` flag, a ``0.0`` confidence). Only None and
    zero-length containers are dropped.
    """
    if value is None:
        return True
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return True
    return False


def _is_graph_id_field(key: str) -> bool:
    """True for raw graph node-id fields stripped at the CLI boundary.

    Boundary-strip rule for the agent-facing surface: the CLI is resolve-first
    (agents pass FQN / simple name / route / topic — never a raw id), so no
    graph-internal id or graph foreign-key column reaches text or JSON. The rule
    is ``key == "id" or key.endswith("_id")``, which catches ``id``,
    ``parent_id`` (SymbolHit), ``chunk_id`` / ``symbol_id`` (SearchHit), and
    ``member_id`` (raw list_clients/list_producers rows), plus any future graph
    FK. No agent-meaningful field in this domain uses the ``_id`` suffix —
    topics are keyed by name, routes by path — so the suffix rule is safe.

    Applied in :func:`project_node` (fields) and :meth:`Envelope.to_json`
    (boundary reshape); the internal envelope + :meth:`Envelope.to_dict` stay
    id-keyed for join/debug use.
    """
    return key == "id" or key.endswith("_id")


def _strip_graph_id_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``node`` with graph-id fields removed (see :func:`_is_graph_id_field`).

    RECURSES into nested dicts and list-of-dicts values so ids embedded in
    sub-records are stripped too — e.g. the ``data`` sub-dict on a
    ``NodeRecord.model_dump()`` (the ``inspect`` envelope node) carries its own
    ``id`` / ``parent_id``; a top-level-only strip would leave them. Scalars and
    non-dict lists pass through unchanged.
    """
    out: dict[str, Any] = {}
    for k, v in node.items():
        if _is_graph_id_field(k):
            continue
        out[k] = _strip_nested_ids(v)
    return out


def _looks_like_raw_graph_id(key: str) -> bool:
    """Heuristic: does this string look like a raw graph node id?

    Used by :meth:`Envelope._to_idfree_dict` to decide whether to synthesize an
    opaque positional key when a node has no semantic identity (see
    :func:`node_key` returning ``None``). Matches 40-hex SHA-1 ids and the
    prefixed hash forms the graph builder emits (``r:phantom:<hex>``,
    ``ucs:<hex>``, ``sym:<hex>``, ``chunk:<hex>``). Meaningful literal keys
    (``"index"``, ``"microservices"``) and handler-built synthetics
    (``microservice:<name>``, ``import:<fqn>``, ``topic:<name>``) do NOT match.
    """
    if not key:
        return False
    if _SHA1_RE.fullmatch(key):
        return True
    # prefixed hash forms: "<prefix>:<optional sub>:<hex-ish>"
    if ":" in key:
        head = key.split(":", 1)[0]
        tail = key.rsplit(":", 1)[-1]
        if head in _GRAPH_ID_PREFIXES and _HEX_TAIL_RE.search(tail):
            return True
    return False


_GRAPH_ID_PREFIXES = frozenset({"r", "ucs", "sym", "chunk", "route", "member"})
_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_HEX_TAIL_RE = re.compile(r"[0-9a-f]{8,}")


def _strip_nested_ids(value: Any) -> Any:
    """Recursively strip graph-id fields from nested dicts / list-of-dicts."""
    if isinstance(value, dict):
        return _strip_graph_id_fields(value)
    if isinstance(value, list):
        return [_strip_nested_ids(item) for item in value]
    return value


def _drop_empty(node: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is ``None`` / ``""`` / ``[]`` / ``{}``.

    Extends the "omit empty optionals" rule from :meth:`Envelope.to_dict` DOWN
    into each node/edge dict so JSON stops serializing ``"symbol_id": null`` /
    ``"role": null`` (the "10 empty fields" complaint). Applied at every detail
    level — no consumer benefits from empty fields, and the text renderers
    already skip missing keys, so this only changes JSON (for the better).
    """
    return {k: v for k, v in node.items() if not _is_empty(v)}


def _compose_file(node: dict[str, Any]) -> dict[str, Any]:
    """Fold raw ``filename`` + ``start_line`` into a display ``file`` field.

    SymbolHit-derived nodes carry ``filename`` / ``start_line`` (raw graph
    columns) that are not display fields. Compose them into one
    ``"filename:start_line"`` string (or just ``filename`` when no line) so the
    ``normal`` tier can show location as a single stable field, then drop the
    raw location columns. Returns the node unchanged (minus raw columns) when
    no ``filename`` is present. Returns a new dict; the input is not mutated.
    """
    filename = str(node.get("filename") or "").strip()
    if not filename:
        return {k: v for k, v in node.items() if k not in _RAW_LOCATION_KEYS}
    start_line = node.get("start_line")
    try:
        file_value = f"{filename}:{int(start_line)}" if start_line not in (None, "") else filename
    except (TypeError, ValueError):
        file_value = filename
    out = {k: v for k, v in node.items() if k not in _RAW_LOCATION_KEYS}
    out["file"] = file_value
    return out


def project_node(node: dict[str, Any], detail: str) -> dict[str, Any]:
    """Project a node dict to the field set for ``detail``.

    * ``"full"``   -> keep every present key (still :func:`_compose_file` +
      :func:`_drop_empty`, so raw location columns become ``file`` and empties
      vanish).
    * ``"normal"`` -> keep ``_NORMAL_NODE_KEYS`` (identity + location +
      classification + ranking). This is the default and the fix for the
      "text too terse" complaint: adds ``file`` / ``score`` / ``role`` /
      ``module``.
    * ``"brief"``  -> keep ``_BRIEF_NODE_KEYS`` (identity only == today's text).

    ``file`` is composed before selection so it is available at ``normal`` /
    ``full``. Empty values are dropped at every level. Graph-id fields (``id``,
    ``*_id``) are stripped at every level via :func:`_strip_graph_id_fields` —
    the CLI is resolve-first, so raw graph ids never reach the agent. Returns a
    new dict.
    """
    composed = _compose_file(node)
    if detail == "full":
        selected = composed
    else:
        allow = _NORMAL_NODE_KEYS if detail == "normal" else _BRIEF_NODE_KEYS
        selected = {k: v for k, v in composed.items() if k in allow}
    return _drop_empty(_strip_graph_id_fields(selected))


def project_edge(edge: dict[str, Any], detail: str) -> dict[str, Any]:
    """Project an edge row to the attr set for ``detail`` (mirrors :func:`project_node`).

    * ``"full"``   -> all attrs.
    * ``"normal"`` -> ``_NORMAL_EDGE_KEYS`` (adds ``mechanism`` / ``role`` /
      ``from_fqn`` over brief).
    * ``"brief"``  -> ``_BRIEF_EDGE_KEYS`` (target id + label + confidence +
      grouping keys == what the text renderers read today).
    """
    if detail == "full":
        selected = edge
    else:
        allow = _NORMAL_EDGE_KEYS if detail == "normal" else _BRIEF_EDGE_KEYS
        selected = {k: v for k, v in edge.items() if k in allow}
    return _drop_empty(selected)


def project_envelope(envelope: Envelope, detail: str) -> Envelope:
    """Return a new Envelope with nodes/edges/candidates projected to ``detail``.

    The single projection seam: :func:`jrag_render.render` calls this once,
    then both the JSON path (``to_json``) and the text renderers consume the
    result. Envelope-level fields (``status`` / ``root`` / ``warnings`` /
    ``truncated`` / ``file_location`` / ``message`` / ``agent_next_actions``)
    are passed through unchanged — they are not node-level and have no detail
    axis. ``detail`` is validated up front so a typo raises instead of
    silently behaving like ``full``.
    """
    if detail not in ("brief", "normal", "full"):
        raise ValueError(
            f"project_envelope: detail must be brief|normal|full, got {detail!r}"
        )
    return Envelope(
        status=envelope.status,
        nodes={nid: project_node(n, detail) for nid, n in envelope.nodes.items()},
        edges=[project_edge(e, detail) for e in envelope.edges],
        root=envelope.root,
        candidates=[project_node(c, detail) for c in envelope.candidates],
        agent_next_actions=list(envelope.agent_next_actions),
        warnings=list(envelope.warnings),
        truncated=envelope.truncated,
        file_location=envelope.file_location,
        message=envelope.message,
        is_external_entrypoint=envelope.is_external_entrypoint,
    )
