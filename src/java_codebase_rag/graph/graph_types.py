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
    symbol_kind: str | None = None
    if kind == "symbol":
        fqn = str(row.get("fqn") or "")
        role = str(row.get("role") or "") or None
        symbol_kind_val = str(row.get("symbol_kind") or row.get("kind") or "").strip()
        symbol_kind = symbol_kind_val or None
    elif kind == "route":
        method = str(row.get("method") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        fqn = f"{method} {path}".strip()
        role = None
    elif kind == "client":
        method = str(row.get("method") or "")
        target = str(row.get("target_service") or "")
        path = str(row.get("path_template") or row.get("path") or "")
        fqn = f"{target} {method} {path}".strip()
        role = None
    else:
        topic = str(row.get("topic") or "")
        broker = str(row.get("broker") or "")
        fqn = f"{topic} {broker}".strip()
        role = None
    return NodeRef(
        id=str(row.get("id") or ""),
        kind=kind,
        fqn=fqn,
        symbol_kind=symbol_kind,
        microservice=str(row.get("microservice") or "") or None,
        module=str(row.get("module") or "") or None,
        role=role,
        generated=bool(row.get("generated")) if row.get("generated") is not None else None,
        generated_by=str(row.get("generated_by")) if row.get("generated_by") else None,
    )


def _to_structured_hints(raw: list[Any]) -> list[StructuredHint]:
    return [StructuredHint(label=h.label, tool=h.tool, args=h.args, actionable=h.actionable, reason=h.reason) for h in raw]
