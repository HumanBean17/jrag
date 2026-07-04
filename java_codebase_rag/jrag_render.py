"""JRAG text rendering (PR-JRAG-1a).

Fresh-built renderer (``cli_format.py`` is styling-primitives only — glyphs and
ANSI — it ships no renderers). The default output is compact text; ``--format
json`` emits the envelope verbatim via :meth:`Envelope.to_json`.

This module imports only the envelope module (which itself imports no heavy
backend modules), so it stays import-safe under the ``build_parser`` lazy
invariant.
"""
from __future__ import annotations

from java_codebase_rag.jrag_envelope import Envelope, simple_name

__all__ = ["render", "tiered_name"]


# Edge labels that carry a ``confidence`` column (CALLS-family). ``conf:`` is
# rendered only for these (PR-JRAG-1a renderer spec). Confirmed against
# java_ontology.EDGE_SCHEMA: CALLS / HTTP_CALLS / ASYNC_CALLS each carry an
# ``EdgeAttr("confidence", "DOUBLE", ...)``; the structural edges
# (EXTENDS/IMPLEMENTS/INJECTS/DECLARES/OVERRIDES/EXPOSES/DECLARES_CLIENT/
# DECLARES_PRODUCER) do not all carry confidence, and even where they do, the
# CALLS-family is what the agent-facing ``conf:`` road-sign is reserved for.
_CALLS_FAMILY_EDGES = frozenset({"CALLS", "HTTP_CALLS", "ASYNC_CALLS"})


def tiered_name(node_id: str, nodes: dict[str, dict]) -> str:
    """Tiered label: simple name -> ``name @service`` -> FQN.

    Falls back through the tiers based on what data the node carries: simple
    name is always available (derived from FQN); ``@service`` is appended when
    ``microservice`` is present; if neither simple name nor service is present,
    the raw FQN is returned.
    """
    node = nodes.get(node_id) or {}
    name = simple_name(node)
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


def _render_listing(envelope: Envelope, *, noun: str) -> str:
    lines: list[str] = []
    for _node_id, node in envelope.nodes.items():
        # Listing omits FQN (PR-JRAG-1a test 11): name + @service only.
        name = simple_name(node)
        service = str(node.get("microservice") or "").strip()
        line = name
        if service:
            line += f"  @{service}"
        lines.append(line)
    if not lines:
        lines.append(f"0 {noun}".rstrip())
    return "\n".join(lines)


def _render_traversal(envelope: Envelope, *, noun: str) -> str:
    lines: list[str] = []
    root_id = envelope.root or ""
    if root_id:
        # root: tiered name (simple name + @service)
        lines.append(f"root: {tiered_name(root_id, envelope.nodes)}")
    if envelope.edges:
        for edge in envelope.edges:
            target_id = _node_id(edge)
            label = tiered_name(target_id, envelope.nodes) if target_id else "(missing)"
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
            lines.append(line)
    else:
        # Zero-results line for a traversal: "0 <noun>  <fqn>  @<service>".
        # The fqn + service come from the root node (the resolved subject).
        parts = [f"0 {noun}".rstrip()]
        root_node = envelope.nodes.get(root_id, {})
        root_fqn = str(root_node.get("fqn") or "").strip()
        root_svc = str(root_node.get("microservice") or "").strip()
        if root_fqn:
            parts.append(root_fqn)
        if root_svc:
            parts.append(f"@{root_svc}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


def _render_inspect(envelope: Envelope) -> str:
    """kv-block renderer for nodes carrying one or more nested dict sections.

    Generic: ANY dict-typed value on a node renders as a header line plus
    indented sorted sub-keys. This is the dispatch signal for the inspect
    shape (PR-JRAG-1a status uses it for ``counts`` / ``edges``; PR-JRAG-3
    ``inspect`` will use it for ``edge_summary`` and other rollups). The
    ``edge_summary`` key is NOT special here - it is reserved for real edge
    data in PR-JRAG-3 and is one of many possible section sources.
    """
    lines: list[str] = []
    for _node_id, node in envelope.nodes.items():
        # ALL dict keys alphabetical (PR-JRAG-1a test 13). A dict-typed value
        # renders in its alphabetical position with a header line followed by
        # indented sorted sub-keys; scalars render inline as ``key: value``.
        for key in sorted(node.keys()):
            val = node[key]
            if isinstance(val, dict) and val:
                lines.append(f"{key}:")
                for ek in sorted(val.keys()):
                    lines.append(f"  {ek}: {val[ek]}")
            else:
                lines.append(f"{key}: {val}")
    return "\n".join(lines)


def _render_ambiguous(envelope: Envelope, *, noun: str) -> str:
    count = len(envelope.candidates)
    header = f"{count} ambiguous matches for {noun!r}" if noun else f"{count} ambiguous matches"
    lines = [header, "Narrow with --kind --java-kind --role --fqn-prefix:"]
    for cand in envelope.candidates:
        # Ambiguous candidates carry reason; NO file / score (PR-JRAG-1a test 14).
        name = simple_name(cand) or str(cand.get("id") or "")
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


def _render_text_shape(envelope: Envelope, *, noun: str, shape: str | None) -> str:
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
    if shape == "inspect":
        return _render_inspect(envelope)
    if envelope.root is not None:
        return _render_traversal(envelope, noun=noun)
    # Listing shape: zero or more node rows. Empty listing renders "0 <noun>".
    if envelope.nodes or noun:
        return _render_listing(envelope, noun=noun)
    return _render_scalar(envelope)


def render(
    envelope: Envelope,
    *,
    fmt: str = "text",
    noun: str = "",
    next_offset: int | None = None,
    shape: str | None = None,
) -> str:
    """Dispatch on ``fmt`` (text default; json emits the envelope verbatim).

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
    if fmt == "json":
        return envelope.to_json()
    body = _render_text_shape(envelope, noun=noun, shape=shape)
    if envelope.truncated:
        hint = _truncated_hint(next_offset=next_offset)
        body = f"{body}\n{hint}" if body else hint
    return body
