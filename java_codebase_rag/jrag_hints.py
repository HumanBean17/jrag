"""JRAG edge-label → CLI-command hint mapper (PR-JRAG-4).

This is the **net-new** module that powers ``envelope.agent_next_actions``. It
maps the graph's edge labels (CALLS, IMPLEMENTS, EXTENDS, INJECTS, OVERRIDES,
OVERRIDDEN_BY, HTTP_CALLS, ASYNC_CALLS — plus composed dot-keys like
``DECLARES.CALLS`` and ``OVERRIDDEN_BY.DECLARES_CLIENT``) to the
``jrag`` command an agent should run next for the resolved root.

Public surface: :func:`next_actions` — keyword-only, returns ``list[str]`` of
``jrag <cmd> <fqn>`` hint strings (≤5, de-duped, zero-direction suppressed).
The function imports :data:`java_ontology.EDGE_SCHEMA` **lazily inside the body**
so :func:`java_codebase_rag.jrag.build_parser` stays pure (no backend imports at
module import time — the sentinel test pins this).
"""
from __future__ import annotations

from typing import Any

__all__ = ["next_actions"]


# Edge label → {direction: jrag_command} map.
#
# Confirmed against java_ontology.EDGE_SCHEMA (java_ontology.py:179) and the
# traversal command surface (PR-JRAG-3a/3b). ``OVERRIDDEN_BY`` is a virtual
# label (the stored edge is ``OVERRIDES``; the describe-time rollup surfaces the
# inbound axis as ``OVERRIDDEN_BY`` for method Symbols — see NodeRecord.edge_summary
# docs at mcp_v2.py:469). HTTP_CALLS / ASYNC_CALLS only fire ``out`` because the
# ``callees`` command dispatches on Client/Producer roots to traverse those edges
# outbound; there is no inbound-only command for them (callers on a Route covers
# the inbound case via a different code path and a different root kind).
_LABEL_COMMANDS: dict[str, dict[str, str]] = {
    "CALLS": {"in": "callers", "out": "callees"},
    "IMPLEMENTS": {"in": "implementations", "out": "hierarchy"},
    "EXTENDS": {"in": "subclasses", "out": "hierarchy"},
    "INJECTS": {"in": "dependents", "out": "dependencies"},
    "OVERRIDES": {"out": "overrides"},
    "OVERRIDDEN_BY": {"in": "overridden-by"},
    "HTTP_CALLS": {"out": "callees"},
    "ASYNC_CALLS": {"out": "callees"},
}

# Cap on returned hints (brief: ≤5). Matches the Envelope.agent_next_actions
# contract and mcp_hints' own cap.
_MAX_HINTS = 5


def _candidate_labels(label: str) -> list[str]:
    """Return the lookup candidates for a (possibly composed) edge label.

    For a plain label (``"CALLS"``): ``["CALLS"]``.
    For ``"OVERRIDDEN_BY.DECLARES_CLIENT"``: the prefix ``"OVERRIDDEN_BY"`` is
      the semantic axis → looked up first.
    For ``"DECLARES.CALLS"``: ``"DECLARES"`` is a rollup prefix with no direct
      command, so the suffix ``"CALLS"`` is the actionable label.
    The full label is always tried first (covers ``"DECLARES_CLIENT"`` if it
    ever appears un-split).
    """
    if "." not in label:
        return [label]
    parts = label.split(".")
    # Full label first (handles un-split composed forms), then prefix, then suffix.
    return [label, parts[0], parts[-1]]


def _lookup_cmd(label: str) -> dict[str, str] | None:
    """Look up a (possibly composed) label in the command map.

    Tries the full label, the dot-prefix, and the dot-suffix. Returns the first
    match or ``None``.
    """
    for cand in _candidate_labels(label):
        cmds = _LABEL_COMMANDS.get(cand)
        if cmds is not None:
            return cmds
    return None


def next_actions(
    *,
    root_fqn: str,
    edge_summary: dict[str, Any] | None = None,
    result_edges: list[dict[str, Any]],
    graph: Any = None,  # noqa: ARG001 — reserved for future use (brief contract)
    current_command: str | None = None,
) -> list[str]:
    """Build ``agent_next_actions`` hints for a resolved root.

    * When ``edge_summary`` is provided (``inspect`` path): iterate each
      ``(label, counts)`` and emit ``jrag <cmd> <fqn>`` for direction ``d`` **only
      when ``counts[d] > 0``** (zero-suppression). Composed dot-keys are covered
      via :func:`_lookup_cmd`.
    * When ``edge_summary`` is ``None`` (traversal path): fall back to the set of
      ``edge_type`` labels present in ``result_edges``. Per-direction counts are
      unavailable, so zero-suppression cannot apply — we emit both directions for
      each recognized label. (The traversal command already filtered to one
      direction; the hints surface the *other* edges the root has, encouraging
      orthogonal exploration.)

    De-dups and caps at ``_MAX_HINTS`` (5). ``graph`` is accepted for forward
    compatibility but not read — all needed data comes from ``edge_summary`` or
    ``result_edges``.
    """
    if not root_fqn:
        return []

    # Lazy import so build_parser() stays pure (PR-JRAG-4 sentinel test).
    # EDGE_SCHEMA is the canonical label set; we use it to skip labels we don't
    # recognize (avoids emitting hints for spurious / future edge types the
    # command map doesn't cover).
    from java_ontology import EDGE_SCHEMA

    # Known virtual labels not in EDGE_SCHEMA (describe-time rollup constructs).
    _VIRTUAL_LABELS = frozenset({"OVERRIDDEN_BY"})

    def _is_known_label(label: str) -> bool:
        base = label.split(".")[0]
        return base in EDGE_SCHEMA or base in _VIRTUAL_LABELS

    hints: list[str] = []
    seen: set[str] = set()

    def _add(cmd: str) -> None:
        hint = f"jrag {cmd} {root_fqn}"
        if hint not in seen:
            seen.add(hint)
            hints.append(hint)

    if edge_summary is not None:
        # inspect path: zero-suppress per direction using counts.
        for label, counts in edge_summary.items():
            if not _is_known_label(str(label)):
                continue
            cmds = _lookup_cmd(str(label))
            if cmds is None:
                continue
            counts_dict = counts if isinstance(counts, dict) else {}
            in_n = int(counts_dict.get("in", 0) or 0)
            out_n = int(counts_dict.get("out", 0) or 0)
            if in_n > 0 and "in" in cmds:
                _add(cmds["in"])
            if out_n > 0 and "out" in cmds:
                _add(cmds["out"])
    else:
        # traversal path: infer from result_edges labels.
        # No per-direction counts → emit both directions for recognized labels,
        # then drop the self-hint (the command an agent just ran). The inverse
        # direction (e.g. `callees` after `callers`) is the useful exploration
        # signal and is kept; only the exact command just run is redundant.
        # ``current_command`` is the jrag subcommand name (``args.command``).
        labels_seen: set[str] = set()
        for edge in result_edges or []:
            et = str(edge.get("edge_type") or "").strip()
            if et and _is_known_label(et):
                labels_seen.add(et.split(".")[0])
        for label in labels_seen:
            cmds = _LABEL_COMMANDS.get(label)
            if cmds is None:
                continue
            for d in ("in", "out"):
                if d in cmds and cmds[d] != current_command:
                    _add(cmds[d])

    return hints[:_MAX_HINTS]
