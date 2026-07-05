"""JRAG text rendering (PR-JRAG-1a).

Fresh-built renderer (``cli_format.py`` is styling-primitives only — glyphs and
ANSI — it ships no renderers). The default output is compact text; ``--format
json`` emits the envelope verbatim via :meth:`Envelope.to_json`.

This module imports only the envelope module (which itself imports no heavy
backend modules), so it stays import-safe under the ``build_parser`` lazy
invariant.
"""
from __future__ import annotations

from typing import Any

from java_codebase_rag.jrag_envelope import Envelope, project_envelope, simple_name

__all__ = ["render", "tiered_name", "display_name"]


# Edge labels that carry a ``confidence`` column (CALLS-family). ``conf:`` is
# rendered only for these (PR-JRAG-1a renderer spec). Confirmed against
# java_ontology.EDGE_SCHEMA: CALLS / HTTP_CALLS / ASYNC_CALLS each carry an
# ``EdgeAttr("confidence", "DOUBLE", ...)``; the structural edges
# (EXTENDS/IMPLEMENTS/INJECTS/DECLARES/OVERRIDES/EXPOSES/DECLARES_CLIENT/
# DECLARES_PRODUCER) do not all carry confidence, and even where they do, the
# CALLS-family is what the agent-facing ``conf:`` road-sign is reserved for.
_CALLS_FAMILY_EDGES = frozenset({"CALLS", "HTTP_CALLS", "ASYNC_CALLS"})

# Route node kinds → short text tag so the routes listing distinguishes HTTP
# endpoints from Kafka topics (otherwise they mash together with no indicator).
# Only route kinds are tagged; symbol/client/producer rows carry other kinds (or
# none) and are left untagged.
_ROUTE_KIND_TAGS: dict[str, str] = {"kafka_topic": "kafka", "http_endpoint": "http"}

# Identity keys already represented in a listing line (display_name + @service +
# kind tag). At ``--detail full`` the per-row kv-block skips these (they are in
# the header line) and renders every OTHER key, so full listing == per-row
# inspect block. ``id`` is absent here AND stripped by the envelope projector's
# graph-id-field rule (see jrag_envelope._strip_graph_id_fields) — listed for
# documentation of the identity set, but the projector is the authoritative
# strip seam. Must agree with the identity half of the envelope projector's
# ``_BRIEF_NODE_KEYS`` (see jrag_envelope.py).
_LISTING_LINE_KEYS: frozenset[str] = frozenset(
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
    }
)

# Fixed left-to-right order for the inline extras appended at ``--detail normal``
# (only the non-empty ones are rendered). Equals the envelope projector's
# ``_NORMAL_NODE_KEYS - _BRIEF_NODE_KEYS``.
_NORMAL_INLINE_EXTRAS: tuple[str, ...] = (
    "module",
    "role",
    "symbol_kind",
    "framework",
    "file",
    "score",
    "explain",
)

# Identity-adjacent extras shown inline at ``--detail brief``. ``score`` is the
# ONLY brief-tier extra because for ranked result sets (``search``) the score
# IS the point — hiding it at brief made ``jrag search --detail brief`` show an
# unranked-looking list. Listing/traversal rows built from NodeRef carry no
# ``score`` field (only SearchHit does), so this is a no-op for non-search
# listings (``find``, routes/clients/producers, traversal target rows).
_BRIEF_INLINE_EXTRAS: tuple[str, ...] = ("score",)

# Edge attrs the edge line already renders (label/confidence); at ``--detail
# full`` these are skipped when appending the remaining attrs inline.
_EDGE_LINE_KEYS: frozenset[str] = frozenset(
    {"other_id", "dst_id", "target_id", "term_id", "edge_type", "stored_edge_type", "label", "type", "confidence"}
)


def _format_inline_value(value: Any) -> str:
    """Format a value for inline rendering: round floats to 3 decimals, others verbatim."""
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _next_action_lines(envelope: Envelope) -> list[str]:
    """Build up to 2 ``next: <hint>`` lines from ``agent_next_actions``.

    Cap at 2 to keep text-mode output token-lean (consistent with the ambiguous
    renderer at :func:`_render_ambiguous`); JSON carries all ≤5. Returns an empty
    list when ``agent_next_actions`` is empty (commands with no root produce no
    hints → nothing appended).
    """
    return [f"next: {hint}" for hint in envelope.agent_next_actions[:2]]


def display_name(node: dict[str, Any]) -> str:
    """Best short label for a node across all kinds (symbol + route/client/producer).

    Listing rows and traversal targets carry different identifying fields per
    kind; this picks the most informative one rather than assuming every node
    has an FQN (routes have ``path``/``method``; clients/producers have
    ``member_fqn`` + ``topic``/``target_service``). Precedence:

      * explicit ``name``  -> symbols (SymbolHit carries one)
      * ``member_fqn``     -> the member making the call/emit, with
                              ``→ topic`` / ``→ target_service`` when present
      * ``path``           -> ``METHOD path`` (route) or ``path`` (client)
      * ``topic``          -> bare topic (producer without a member)
      * ``fqn``            -> fqn-derived simple name (classes/methods)

    Returns ``""`` only when nothing identifiable is present.

    For a method symbol (``pkg.Class#method(args)``) the label is
    ``Class#method``, NOT the bare ``name``: ``getId`` / ``process`` / ``create``
    collide across classes, so a traversal/listing row reduced to the bare
    method name is ambiguous (the SlaService callees example — four ``getId``,
    five ``process``). The declaring class is identity-level disambiguation, so
    it folds into the label at every detail tier (brief included). ``name`` (the
    clean method name, no args) is preferred when present; the FQN-derived method
    name is the fallback when ``name`` is absent.
    """
    fqn = str(node.get("fqn") or "").strip()
    if "#" in fqn:
        head, _, tail = fqn.partition("#")
        cls = head.rsplit(".", 1)[-1]
        raw_name = str(node.get("name") or "").strip()
        # Some backends populate `name` with the full ``Class#method(args)``
        # form already (ambiguous-resolve candidates do this). Prepending
        # ``cls`` would double it (``Class#Class#method``); use it verbatim —
        # it already carries the declaring class plus args, so it stays
        # identity-unique. Traversal method nodes carry the bare clean name
        # (no ``#``), so they still take the ``Class#method`` path below.
        if raw_name and "#" in raw_name:
            return raw_name
        method = raw_name or tail.split("(", 1)[0]
        if cls and method:
            return f"{cls}#{method}"
    name = str(node.get("name") or "").strip()
    if name:
        return name
    member_fqn = str(node.get("member_fqn") or "").strip()
    if member_fqn:
        base = member_fqn.rsplit(".", 1)[-1]
        topic = str(node.get("topic") or "").strip()
        if topic:
            return f"{base} → {topic}"
        target = str(node.get("target_service") or "").strip()
        if target:
            return f"{base} → {target}"
        return base
    path = str(node.get("path") or "").strip()
    if path:
        method = str(node.get("method") or "").strip()
        return f"{method} {path}" if method else path
    topic = str(node.get("topic") or "").strip()
    if topic:
        return topic
    # Symbol / fallback: fqn-derived simple name.
    return simple_name(node)


def tiered_name(node_id: str, nodes: dict[str, dict]) -> str:
    """Tiered label: ``display_name @service`` -> display_name -> FQN -> id.

    ``display_name`` covers symbols (fqn) AND route/client/producer nodes
    (path/member_fqn/topic). ``@service`` is appended when ``microservice`` is
    present; if the node still yields no label, the raw FQN (then the id) is
    returned so a traversal target is never rendered empty.
    """
    node = nodes.get(node_id) or {}
    name = display_name(node)
    service = str(node.get("microservice") or "").strip()
    if name and service:
        return f"{name} @{service}"
    if name:
        return name
    fqn = str(node.get("fqn") or "").strip()
    return fqn or node_id


def _node_id(edge: dict) -> str:
    """Pull the *other-end* node id out of an edge row across backend variants.

    ``neighbors_v2`` returns ``other_id``; traversal LadybugGraph methods return
    one of ``dst_id`` / ``target_id`` / ``term_id``. We try them in order.
    """
    for key in ("other_id", "dst_id", "target_id", "term_id"):
        val = edge.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _edge_label(edge: dict) -> str:
    for key in ("edge_type", "stored_edge_type", "label", "type"):
        val = edge.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _truncated_hint(*, next_offset: int | None) -> str:
    if next_offset is not None:
        return f"truncated: more results — use --offset {next_offset}"
    return "truncated: more results — narrow your query"


def _render_error(envelope: Envelope) -> str:
    msg = envelope.message or (envelope.warnings[0] if envelope.warnings else "error")
    return f"error: {msg}"


def _render_not_found(envelope: Envelope) -> str:
    msg = envelope.message or "not found"
    return f"not found: {msg}"


def _render_listing(envelope: Envelope, *, noun: str, detail: str = "normal") -> str:
    lines: list[str] = []
    for _node_id, node in envelope.nodes.items():
        # Listing omits FQN (PR-JRAG-1a test 11): display_name + @service only.
        # display_name handles routes (METHOD path) / clients / producers, which
        # carry no FQN — simple_name would render them blank.
        name = display_name(node)
        if not name:
            # Unresolved brownfield routes can carry empty path+topic+member;
            # fall back to the file basename (then a placeholder) so the row
            # never renders as a blank line or a bare ``@service``. The
            # projector composes raw filename+start_line into ``file``, so check
            # both ``file`` and the raw ``filename`` (present pre-projection /
            # when no start_line was carried).
            label = ""
            for key in ("file", "filename"):
                raw = str(node.get(key) or "").strip()
                if raw:
                    base = raw.rsplit(":", 1)[0] if raw.rsplit(":", 1)[-1].isdigit() else raw
                    label = base.rsplit("/", 1)[-1]
                    break
            name = label or "(no identifier)"
        service = str(node.get("microservice") or "").strip()
        tag = _ROUTE_KIND_TAGS.get(str(node.get("kind") or ""))
        parts: list[str] = [f"[{tag}]", name] if tag else [name]
        line = "  ".join(parts)
        if service:
            line += f"  @{service}"
        # PR-JRAG-3b: distinguish unresolved imports from resolved graph nodes
        # in TEXT mode. Without this marker, `imports <file>` renders resolved
        # Symbols and unresolved placeholders identically (only JSON carries
        # the resolved flag), leaving a text-mode agent unable to tell which
        # imports resolved. The marker is gated on the synthetic
        # `kind="unresolved_import"` set by _cmd_imports.
        if node.get("kind") == "unresolved_import":
            line += "  (unresolved)"
        # detail > brief: surface the fields the terse line drops. The projector
        # has already trimmed the node to the requested field set, so we only
        # decide PRESENTATION. brief = append identity-adjacent extras that
        # matter even at the terse tier (score on search hits — without it the
        # ranking is invisible). normal = append inline location/classification/
        # ranking extras to the SAME line (one line per row — the fix for "text
        # too terse": adds module/role/file/score). full = per-row inspect block
        # of every non-identity key (signature/annotations/snippet/...).
        if detail == "brief":
            extras = [
                f"{key}={_format_inline_value(node[key])}"
                for key in _BRIEF_INLINE_EXTRAS
                if key in node and node[key] not in ("", None)
            ]
            if extras:
                line += "  " + "  ".join(extras)
        elif detail == "normal":
            extras = [
                f"{key}={_format_inline_value(node[key])}"
                for key in _NORMAL_INLINE_EXTRAS
                if key in node and node[key] not in ("", None)
            ]
            if extras:
                line += "  " + "  ".join(extras)
        lines.append(line)
        if detail == "full":
            rest = {k: v for k, v in node.items() if k not in _LISTING_LINE_KEYS}
            if rest:
                lines.extend(_render_inspect_block(rest, 1))
    if not lines:
        lines.append(f"0 {noun}".rstrip())
    # Listing breadcrumbs (Phase 2): <=2 `next:` hint lines when the listing
    # command emitted agent_next_actions (routes/clients/producers/topics).
    lines.extend(_next_action_lines(envelope))
    return "\n".join(lines)


def _node_normal_extras(node: dict[str, Any]) -> str:
    """Inline ``key=value`` extras for a node at ``normal`` detail.

    Mirrors :func:`_render_listing`'s normal-tier inline append exactly (same
    ``_NORMAL_INLINE_EXTRAS`` key list / format) so a traversal row and a listing
    row show the SAME fields at the same level, and so text matches the field
    set JSON carries at ``normal``. Returns ``""`` when none of the extras are
    present (the line is left unchanged).
    """
    extras = [
        f"{key}={node[key]}"
        for key in _NORMAL_INLINE_EXTRAS
        if key in node and node[key] not in ("", None)
    ]
    return ("  " + "  ".join(extras)) if extras else ""


def _node_full_rows(node: dict[str, Any], indent: int) -> list[str]:
    """Indented kv-block of a node's non-identity fields at ``full`` detail.

    Mirrors :func:`_render_listing`'s full-tier block: identity keys
    (``_LISTING_LINE_KEYS`` — already represented in the label/@service line) are
    skipped, the rest recurse via :func:`_render_inspect_block` so
    signature / annotations / modifiers / package render as readable nested kv
    lines. Returns ``[]`` when the node has no content fields.
    """
    rest = {k: v for k, v in node.items() if k not in _LISTING_LINE_KEYS}
    return _render_inspect_block(rest, indent) if rest else []


def _format_edge_rows(edge: dict, nodes: dict[str, dict], *, detail: str = "normal") -> list[str]:
    """Format an edge as one header line plus (at ``full``) a per-edge block.

    Shared across all render modes (flat + grouped). The header is
    ``  <tiered name>`` plus ``conf=N.NN`` for CALLS-family edges. The caller is
    responsible for any grouping header above these rows.

    NODE-level detail is honored symmetrically with :func:`_render_listing`
    (PR-JRAG-6 fixed listings but not traversals; this closes that gap so
    ``jrag callees`` text carries the same fields as its JSON, and ``--detail
    full`` is no longer a no-op):

    * ``brief``  -> header only (label + conf). Identity only; the label already
      carries the declaring class for methods via :func:`display_name`.
    * ``normal`` -> header + edge ``mechanism`` + the target node's
      ``_NORMAL_INLINE_EXTRAS`` (module/role/symbol_kind/framework/file/score)
      inline — the same inline append listings use.
    * ``full``   -> header + every remaining EDGE attr inline (annotation /
      field_or_param / from_fqn / …) + a per-edge indented block of the target
      node's content fields (signature/annotations/modifiers/...).
    """
    target_id = _node_id(edge)
    target = nodes.get(target_id) or {}
    label = tiered_name(target_id, nodes) if target_id else "(missing)"
    line = f"  {label}"
    edge_type = _edge_label(edge)
    # conf: only on CALLS-family edges (PR-JRAG-1a test 12).
    if edge_type in _CALLS_FAMILY_EDGES:
        conf = edge.get("confidence")
        if conf is not None:
            try:
                line += f"  conf={float(conf):.2f}"
            except (TypeError, ValueError):
                pass
    if detail == "normal":
        mech = edge.get("mechanism")
        if mech not in ("", None):
            line += f"  mechanism={mech}"
        line += _node_normal_extras(target)
        return [line]
    if detail == "full":
        for key in edge:
            if key in _EDGE_LINE_KEYS:
                continue
            val = edge.get(key)
            if val in ("", None):
                continue
            line += f"  {key}={val}"
        rows = [line]
        rows.extend(_node_full_rows(target, 2))
        return rows
    return [line]


def _render_traversal(envelope: Envelope, *, noun: str, detail: str = "normal") -> str:
    lines: list[str] = []
    root_id = envelope.root or ""
    if root_id:
        # root: tiered name (Class / Class#method + @service). At normal the
        # root node's module/role/file/score append inline; at full a kv-block
        # renders under it — the SAME detail contract as an edge-target row, so
        # the resolved-subject line carries the same fields JSON shows (parity
        # with the listing/edge detail work; pre-fix the root was always bare).
        root_node = envelope.nodes.get(root_id, {})
        root_label = tiered_name(root_id, envelope.nodes)
        if detail == "normal":
            lines.append(f"root: {root_label}{_node_normal_extras(root_node)}")
        elif detail == "full":
            lines.append(f"root: {root_label}")
            lines.extend(_node_full_rows(root_node, 1))
        else:
            lines.append(f"root: {root_label}")
    if not envelope.edges:
        # Zero-results line for a traversal: "0 <noun>  <fqn>  @<service>".
        # The fqn + service come from the root node (the resolved subject). When
        # the producer flagged the root as a server-exposed entrypoint with no
        # in-repo callers, lead with that honest note instead of the bare,
        # bug-looking "0 <noun>" — the empty result is correct here.
        root_node = envelope.nodes.get(root_id, {})
        root_fqn = str(root_node.get("fqn") or "").strip()
        root_svc = str(root_node.get("microservice") or "").strip()
        if envelope.is_external_entrypoint:
            parts = ["external entrypoint — no in-repo callers"]
        else:
            parts = [f"0 {noun}".rstrip()]
        if root_fqn:
            parts.append(root_fqn)
        if root_svc:
            parts.append(f"@{root_svc}")
        lines.append("  ".join(parts))
        lines.extend(_next_action_lines(envelope))
        return "\n".join(lines)

    # Grouped rendering fires ONLY when the producer attached the grouping
    # key (hierarchy sets `direction`; decompose sets `stage`; connection sets
    # `section`). Other traversals (callers/callees/dependents/...) leave all
    # three unset and fall through to the flat list below — current behavior
    # unchanged (Fix 1).
    has_stages = any(e.get("stage") is not None for e in envelope.edges)
    has_direction = any(e.get("direction") for e in envelope.edges)
    has_section = any(e.get("section") for e in envelope.edges)

    if has_section:
        # connection: group under inbound:/outbound: headers. Edges carry a
        # `section` key set to "inbound" or "outbound" by _cmd_connection.
        # Unknown section values are rendered under their literal name so the
        # agent sees the data even if a future caller adds a new section.
        in_sec = [e for e in envelope.edges if e.get("section") == "inbound"]
        out_sec = [e for e in envelope.edges if e.get("section") == "outbound"]
        other = [e for e in envelope.edges if e.get("section") not in ("inbound", "outbound")]
        if in_sec:
            lines.append("inbound:")
            for e in in_sec:
                lines.extend(_format_edge_rows(e, envelope.nodes, detail=detail))
        if out_sec:
            lines.append("outbound:")
            for e in out_sec:
                lines.extend(_format_edge_rows(e, envelope.nodes, detail=detail))
        for e in other:
            section = str(e.get("section") or "")
            if section:
                lines.append(f"{section}:")
            lines.extend(_format_edge_rows(e, envelope.nodes, detail=detail))
        lines.extend(_next_action_lines(envelope))
        return "\n".join(lines)

    if has_stages:
        # decompose role-waterfall: group edges under `stage N` headers.
        # The role on each edge (carried from StageSymbol) labels the stage
        # when homogeneous; otherwise we just number it.
        stage_order: list[int] = []
        by_stage: dict[int, list[dict]] = {}
        for e in envelope.edges:
            s = int(e.get("stage") or 0)
            if s not in by_stage:
                by_stage[s] = []
                stage_order.append(s)
            by_stage[s].append(e)
        for s in stage_order:
            stage_edges = by_stage[s]
            roles = {str(e.get("role") or "").upper() for e in stage_edges if e.get("role")}
            if s == 0:
                header = "stage 0 (seed):"
            elif len(roles) == 1:
                header = f"stage {s} ({next(iter(roles)).lower()}):"
            else:
                header = f"stage {s}:"
            lines.append(header)
            for e in stage_edges:
                lines.extend(_format_edge_rows(e, envelope.nodes, detail=detail))
        lines.extend(_next_action_lines(envelope))
        return "\n".join(lines)

    if has_direction:
        # hierarchy tree: group under ↑ supertypes / ↓ subtypes headers.
        up = [e for e in envelope.edges if e.get("direction") == "up"]
        dn = [e for e in envelope.edges if e.get("direction") == "down"]
        if up:
            lines.append("↑ supertypes:")
            for e in up:
                lines.extend(_format_edge_rows(e, envelope.nodes, detail=detail))
        if dn:
            lines.append("↓ subtypes:")
            for e in dn:
                lines.extend(_format_edge_rows(e, envelope.nodes, detail=detail))
        lines.extend(_next_action_lines(envelope))
        return "\n".join(lines)

    # Flat: callers / callees / implementations / subclasses / overrides /
    # overridden-by / dependents / impact / flow (current behavior).
    for edge in envelope.edges:
        lines.extend(_format_edge_rows(edge, envelope.nodes, detail=detail))
    lines.extend(_next_action_lines(envelope))
    return "\n".join(lines)


def _inspect_inline(val: Any) -> str:
    """One-line rendering for a leaf value or a collapsed list/dict item.

    Scalars render as themselves; a list of scalars joins with ``, ``; a dict
    collapses to ``k: v, k: v`` (used for list-of-dict sample items, which are
    short). Empty list/dict render as ``[]`` / ``{}``.
    """
    if isinstance(val, list):
        return ", ".join(_inspect_inline(x) for x in val) if val else "[]"
    if isinstance(val, dict):
        return ", ".join(f"{k}: {_inspect_inline(v)}" for k, v in val.items()) if val else "{}"
    if isinstance(val, str):
        return val
    return str(val)


def _is_dict_list(v: Any) -> bool:
    """True for a non-empty list whose every item is a dict (rendered as blocks)."""
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _render_inspect_block(node: dict[str, Any], indent: int) -> list[str]:
    """Recursively render a dict's keys as indented kv lines.

    dict -> header + recurse (so ``counts: {svc: {kind: n}}`` nests fully);
    non-empty list-of-dicts -> header + one ``- <inline item>`` line per entry
    (sample lists like ``client_sample``/``route_sample``); other lists and
    scalars -> inline. Replaces the old single-level renderer that printed
    nested dicts and list-of-dicts as Python ``repr()``.
    """
    pad = "  " * indent
    out: list[str] = []
    for key in sorted(node.keys(), key=str):
        val = node[key]
        if isinstance(val, dict) and val:
            out.append(f"{pad}{key}:")
            out.extend(_render_inspect_block(val, indent + 1))
        elif _is_dict_list(val):
            out.append(f"{pad}{key}:")
            for item in val:
                out.append(f"{pad}  - {_inspect_inline(item)}")
        else:
            out.append(f"{pad}{key}: {_inspect_inline(val)}")
    return out


def _render_inspect(envelope: Envelope) -> str:
    """kv-block renderer for nodes carrying one or more nested dict sections.

    Generic: ANY dict-typed value on a node renders as a header line plus
    indented sorted sub-keys, recursing fully. This is the dispatch signal for
    the inspect shape (PR-JRAG-1a status uses it for ``counts`` / ``edges``;
    PR-JRAG-3 ``inspect`` uses it for ``edge_summary`` and other rollups). The
    ``edge_summary`` key is NOT special here - it is reserved for real edge
    data in PR-JRAG-3 and is one of many possible section sources.
    """
    lines: list[str] = []
    for _node_id, node in envelope.nodes.items():
        # ALL dict keys alphabetical (PR-JRAG-1a test 13); nested dicts and
        # list-of-dicts recurse via _render_inspect_block instead of repr().
        lines.extend(_render_inspect_block(node, 0))
    lines.extend(_next_action_lines(envelope))
    return "\n".join(lines)


def _render_ambiguous(envelope: Envelope, *, noun: str) -> str:
    count = len(envelope.candidates)
    header = f"{count} ambiguous matches for {noun!r}" if noun else f"{count} ambiguous matches"
    lines = [header, "Narrow with --kind --java-kind --role --fqn-contains:"]
    for cand in envelope.candidates:
        # Ambiguous candidates carry reason; NO file / score (PR-JRAG-1a test 14).
        # display_name only — graph id is NOT a fallback (the envelope projector
        # strips id/parent_id at every detail level; an unidentified candidate
        # renders with "(no identifier)" rather than leaking a raw SHA).
        name = display_name(cand) or "(no identifier)"
        service = str(cand.get("microservice") or "").strip()
        reason = str(cand.get("reason") or "").strip()
        line = f"  {name}"
        if service:
            line += f"  @{service}"
        if reason:
            line += f"  ({reason})"
        lines.append(line)
    # <=2 next: hints; no auto-pick (PR-JRAG-1a renderer spec).
    for hint in envelope.agent_next_actions[:2]:
        lines.append(f"next: {hint}")
    return "\n".join(lines)


def _render_scalar(envelope: Envelope) -> str:
    if envelope.message is not None:
        return envelope.message
    if envelope.warnings:
        return "\n".join(envelope.warnings)
    return envelope.status


def _render_text_shape(envelope: Envelope, *, noun: str, shape: str | None, detail: str = "normal") -> str:
    if envelope.status == "error":
        return _render_error(envelope)
    if envelope.status == "not_found":
        return _render_not_found(envelope)
    if envelope.status == "ambiguous":
        return _render_ambiguous(envelope, noun=noun)
    # status == "ok": dispatch on EXPLICIT shape hint first, then envelope
    # structure. The shape hint is the only path to ``_render_inspect`` -
    # listing nodes typically carry dict-valued fields after ``.model_dump()``
    # (Symbol nodes have ``source_range`` / ``annotations`` / ``capabilities``
    # / ``metadata`` etc.), so inferring inspect from "any node has a dict
    # value" would silently mis-render listings as inspect (FQN alphabetical).
    # Inspect is declared by the caller, never guessed from node contents.
    #
    # Traversal shape: a root subject is set (the resolved node the edges are
    # relative to). This is true even when the traversal produced zero edges
    # — the zero-edges traversal line is "0 <noun>  <fqn>  @<service>", NOT
    # the scalar fallback.
    #
    # Precedence: explicit ``shape="inspect"`` wins over ``root``/listing
    # by intent (callers declare what they want); then ``root`` wins over
    # listing (a root signals "edges are the story").
    #
    # detail: the envelope passed in is ALREADY projected (see :func:`render`),
    # so each renderer sees only the keys for its detail level. ``detail`` is
    # threaded in only to choose PRESENTATION (inline vs block / which edge
    # attrs to print) — the field-set decision was made once, up front, by
    # :func:`project_envelope`. ``_render_inspect`` needs no ``detail`` kwarg:
    # it renders whatever keys survived projection (few at brief, all at full).
    if shape == "inspect":
        return _render_inspect(envelope)
    if envelope.root is not None:
        return _render_traversal(envelope, noun=noun, detail=detail)
    # Listing shape: zero or more node rows. Empty listing renders "0 <noun>".
    if envelope.nodes or noun:
        return _render_listing(envelope, noun=noun, detail=detail)
    return _render_scalar(envelope)


def render(
    envelope: Envelope,
    *,
    fmt: str = "text",
    detail: str = "normal",
    noun: str = "",
    next_offset: int | None = None,
    shape: str | None = None,
) -> str:
    """Dispatch on ``fmt`` (text default; json emits the projected envelope).

    ``detail`` (``brief`` / ``normal`` / ``full``, default ``normal``) is
    ORTHOGONAL to ``fmt``: the envelope is projected to the requested field set
    ONCE via :func:`project_envelope`, then BOTH the JSON path (``to_json``)
    and the text renderers consume the projected result. So ``--format json
    --detail brief`` and ``--format text --detail brief`` go through the same
    field set. ``brief`` reproduces today's terse text; ``normal`` adds
    ``module``/``role``/``symbol_kind``/``framework``/``file``/``score`` (the
    fix for "text too terse"); ``full`` keeps everything (incl. ``snippet`` /
    ``signature`` / ``annotations``) and drops empty fields at all levels.

    ``noun`` is the human-readable noun for the result kind (e.g. ``"callers"``,
    ``"matches"``); used in zero-results and ambiguous headers. ``next_offset``
    selects the truncated hint: ``None`` -> ``narrow your query`` (no offset
    support on this command); a number -> ``use --offset <N>`` (find/search).

    ``shape`` is the EXPLICIT render-shape hint. The only accepted value today
    is ``"inspect"`` (kv-block + indented alphabetical sections); callers that
    need it declare it (PR-JRAG-1a ``status``, future PR-JRAG-1b/3 ``inspect``).
    ``None`` falls back to structural inference: ``root`` -> traversal,
    ``nodes``/``noun`` -> listing, else scalar. Listing nodes frequently carry
    dict-valued fields after ``.model_dump()``, so inspect is NEVER inferred
    from node contents - only an explicit ``shape="inspect"`` routes there.
    """
    projected = project_envelope(envelope, detail)
    if fmt == "json":
        return projected.to_json()
    body = _render_text_shape(projected, noun=noun, shape=shape, detail=detail)
    if projected.truncated:
        hint = _truncated_hint(next_offset=next_offset)
        body = f"{body}\n{hint}" if body else hint
    # Warnings are rendered in text mode (one ``warning:`` line each) so an
    # agent running without ``--format json`` still sees inapplicable-flag /
    # post-filter notices. Without this the warnings[] field was JSON-only and
    # the "inapplicable flags never silently ignored" spec was effectively
    # unenforced for text consumers.
    if projected.warnings:
        warning_lines = "\n".join(f"warning: {w}" for w in projected.warnings)
        body = f"{body}\n{warning_lines}" if body else warning_lines
    return body
