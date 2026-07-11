"""Shared graph types and helpers used by mcp_v2 and resolve_service.

This is the neutral, acyclic shared module. It must NOT import from
``mcp_v2`` or ``resolve_service`` — both of those import FROM here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from java_codebase_rag.graph.ladybug_queries import LadybugGraph
from java_codebase_rag.mcp.mcp_hints import generate_hints

__all__ = [
    "NodeRef",
    "StructuredHint",
    "set_hints_enabled",
    "_hints_or_skip",
    "_node_kind_from_id",
    "_resolve_node_kind",
    "_node_ref_from_row",
    "_to_structured_hints",
]


class NodeRef(BaseModel):
    id: str
    kind: Literal["symbol", "route", "client", "producer", "unresolved_call_site"]
    fqn: str
    name: str | None = None
    symbol_kind: str | None = None
    microservice: str | None = None
    module: str | None = None
    role: str | None = None
    generated: bool | None = None
    generated_by: str | None = None
    # Identity-adjacent fields consumed by ``jrag_envelope.node_key`` (keying)
    # and ``project_node`` allow-lists (rendering) so find filter-mode nodes
    # carry the same rich shape as the dedicated listings (http-routes /
    # http-clients / producers). All optional with default None: existing
    # NodeRef constructors are unaffected and ``_drop_empty`` strips unset
    # fields at the JSON boundary.
    method: str | None = None
    path: str | None = None
    framework: str | None = None
    member_fqn: str | None = None
    target_service: str | None = None
    client_kind: str | None = None
    topic: str | None = None
    broker: str | None = None
    producer_kind: str | None = None
    filename: str | None = None
    start_line: int | None = None
    resolved: bool | None = None


class StructuredHint(BaseModel):
    label: str = ""
    tool: Literal["search", "find", "describe", "neighbors", "resolve"]
    args: dict[str, Any]
    actionable: bool = True
    reason: str = ""


# Module-level flag set by server.py at startup from resolved config.
# Single source of truth — both mcp_v2 and resolve_service read this via
# _hints_or_skip, and server.py sets it via set_hints_enabled (re-exported
# by mcp_v2 for back-comat).
_hints_enabled: bool = True


def set_hints_enabled(enabled: bool) -> None:
    global _hints_enabled
    _hints_enabled = enabled


def _hints_or_skip(tool: str, payload: dict) -> tuple[list, list]:
    return generate_hints(tool, payload) if _hints_enabled else ([], [])


def _node_kind_from_id(
    id_str: str,
) -> Literal["symbol", "route", "client", "producer", "unresolved_call_site"]:
    if id_str.startswith("ucs:"):
        return "unresolved_call_site"
    if id_str.startswith("sym:"):
        return "symbol"
    if id_str.startswith("route:") or id_str.startswith("r:"):
        return "route"
    if id_str.startswith("client:") or id_str.startswith("c:"):
        return "client"
    if id_str.startswith("producer:") or id_str.startswith("p:"):
        return "producer"
    raise ValueError(f"Unknown id prefix for `{id_str}`")


def _resolve_node_kind(
    graph: LadybugGraph,
    node_id: str,
) -> Literal["symbol", "route", "client", "producer", "unresolved_call_site"]:
    try:
        return _node_kind_from_id(node_id)
    except ValueError:
        pass
    if graph._rows("MATCH (n:Symbol) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "symbol"
    if graph._rows("MATCH (n:Route) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "route"
    if graph._rows("MATCH (n:Client) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "client"
    if graph._rows("MATCH (n:Producer) WHERE n.id = $id RETURN n.id AS id LIMIT 1", {"id": node_id}):  # noqa: SLF001
        return "producer"
    raise ValueError(f"Unknown id prefix for `{node_id}`")


def _node_ref_from_row(kind: Literal["symbol", "route", "client", "producer"], row: dict[str, Any]) -> NodeRef:
    """Map a graph store row to a :class:`NodeRef`.

    ``fqn`` is set to each kind's documented natural identifier (README
    §"jrag — agent CLI") so ``jrag_envelope.node_key`` (which checks ``fqn``
    first) keys nodes correctly and no raw graph id leaks:

      * symbol  -> symbol fqn
      * route   -> ``"METHOD path"`` (HTTP endpoint); ``"topic:<name>"`` when a
                   kafka topic surfaces as :Route; ``""`` for a phantom route
                   (node_key then falls through to the composed ``file``)
      * client  -> ``"member_fqn->target_service"``
      * producer-> ``"topic:<name>"``

    Rich detail (member_fqn / target_service / method / path / topic /
    framework / filename / start_line / resolved) is populated alongside so
    find filter-mode nodes render with the same shape as the dedicated
    listings (``http-routes`` / ``http-clients`` / ``producers``) instead of
    collapsing to ``{"kind": ...}`` after the envelope's projection+drop-empty.
    """
    microservice = str(row.get("microservice") or "") or None
    module = str(row.get("module") or "") or None
    filename = str(row.get("filename") or "") or None
    start_line = row.get("start_line")
    try:
        start_line = int(start_line) if start_line not in (None, "") else None
    except (TypeError, ValueError):
        start_line = None
    resolved_raw = row.get("resolved")
    resolved = bool(resolved_raw) if resolved_raw is not None else None
    nid = str(row.get("id") or "")

    if kind == "symbol":
        fqn = str(row.get("fqn") or "")
        role = str(row.get("role") or "") or None
        symbol_kind_val = str(row.get("symbol_kind") or row.get("kind") or "").strip()
        symbol_kind = symbol_kind_val or None
        return NodeRef(
            id=nid, kind="symbol", fqn=fqn,
            name=str(row.get("name") or "") or None,
            symbol_kind=symbol_kind,
            microservice=microservice, module=module, role=role,
            filename=filename, start_line=start_line,
            generated=bool(row.get("generated")) if row.get("generated") is not None else None,
            generated_by=str(row.get("generated_by")) if row.get("generated_by") else None,
        )

    if kind == "route":
        method = str(row.get("method") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        topic = str(row.get("topic") or "") or None
        if method or path:
            fqn = f"{method} {path}".strip()
        elif topic:
            fqn = f"topic:{topic}"
        else:
            fqn = ""
        return NodeRef(
            id=nid, kind="route", fqn=fqn, name=(path or topic),
            method=method or None, path=path or None, topic=topic,
            framework=str(row.get("framework") or "") or None,
            microservice=microservice, module=module,
            filename=filename, start_line=start_line, resolved=resolved,
        )

    if kind == "client":
        mfqn = str(row.get("member_fqn") or "")
        tgt = str(row.get("target_service") or "")
        method = str(row.get("method") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        member_fqn = mfqn or None
        target_service = tgt or None
        member_simple = (mfqn.rsplit(".", 1)[-1] if mfqn else "") or None
        # Build the contract id ``member_fqn->target_service`` from the RAW
        # strings: ``member_fqn`` was normalized to None above, so interpolating
        # it would emit ``"None-><target>"`` for member-less (brownfield/meta)
        # clients. Fall back to whichever half is present; never a dangling arrow.
        fqn = f"{mfqn}->{tgt}" if (mfqn and tgt) else (mfqn or tgt)
        return NodeRef(
            id=nid, kind="client",
            fqn=fqn,
            name=member_simple,
            member_fqn=member_fqn, target_service=target_service,
            method=method or None, path=path or None,
            client_kind=str(row.get("client_kind") or "") or None,
            microservice=microservice, module=module,
            filename=filename, start_line=start_line, resolved=resolved,
        )

    # producer
    topic = str(row.get("topic") or "") or None
    member_fqn = str(row.get("member_fqn") or "") or None
    member_simple = (member_fqn.rsplit(".", 1)[-1] if member_fqn else "") or None
    return NodeRef(
        id=nid, kind="producer",
        fqn=(f"topic:{topic}" if topic else ""),
        name=(topic or member_simple),
        topic=topic, broker=str(row.get("broker") or "") or None,
        producer_kind=str(row.get("producer_kind") or "") or None,
        member_fqn=member_fqn,
        microservice=microservice, module=module,
        filename=filename, start_line=start_line, resolved=resolved,
    )


def _to_structured_hints(raw: list[Any]) -> list[StructuredHint]:
    return [StructuredHint(label=h.label, tool=h.tool, args=h.args, actionable=h.actionable, reason=h.reason) for h in raw]
