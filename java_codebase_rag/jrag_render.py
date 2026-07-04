"""JRAG text rendering (PR-JRAG-1a).

Fresh-built renderer (``cli_format.py`` is styling-primitives only — glyphs and
ANSI — it ships no renderers). The default output is compact text; ``--format
json`` emits the envelope verbatim via :meth:`Envelope.to_json`.

This module imports only the envelope module (which itself imports no heavy
backend modules), so it stays import-safe under the ``build_parser`` lazy
invariant.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from java_codebase_rag.jrag_envelope import Envelope, simple_name

if TYPE_CHECKING:
    pass

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
    lines: list[str] = []
    for _node_id, node in envelope.nodes.items():
        # ALL dict keys alphabetical (PR-JRAG-1a test 13). The ``edge_summary``
        # key, if present, is rendered in its alphabetical position with a
        # header line followed by indented sorted keys.
        for key in sorted(node.keys()):
            val = node[key]
            if key == "edge_summary" and isinstance(val, dict) and val:
                lines.append("edge_summary:")
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


def _render_text_shape(envelope: Envelope, *, noun: str) -> str:
    if envelope.status == "error":
        return _render_error(envelope)
    if envelope.status == "not_found":
        return _render_not_found(envelope)
    if envelope.status == "ambiguous":
        return _render_ambiguous(envelope, noun=noun)
    # status == "ok": dispatch on envelope shape.
    # Traversal shape: a root subject is set (the resolved node the edges are
    # relative to). This is true even when the traversal produced zero edges
    # — the zero-edges traversal line is "0 <noun>  <fqn>  @<service>", NOT
    # the scalar fallback.
    if envelope.root is not None:
        return _render_traversal(envelope, noun=noun)
    # Inspect shape: at least one node carries edge_summary.
    if envelope.nodes and any(
        isinstance(n.get("edge_summary"), dict) for n in envelope.nodes.values()
    ):
        return _render_inspect(envelope)
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
) -> str:
    """Dispatch on ``fmt`` (text default; json emits the envelope verbatim).

    ``noun`` is the human-readable noun for the result kind (e.g. ``"callers"``,
    ``"matches"``); used in zero-results and ambiguous headers. ``next_offset``
    selects the truncated hint: ``None`` -> ``narrow your query`` (no offset
    support on this command); a number -> ``use --offset <N>`` (find/search).
    """
    if fmt == "json":
        return envelope.to_json()
    body = _render_text_shape(envelope, noun=noun)
    if envelope.truncated:
        hint = _truncated_hint(next_offset=next_offset)
        body = f"{body}\n{hint}" if body else hint
    return body
