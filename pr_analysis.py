"""Unified-diff → symbol mapping and PR-style risk scoring (B4 / PR-B).

Uses the `unidiff` library for parsing. Graph-resident symbols only; newly
added Java members are not modelled — see `notes` on the returned report.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from kuzu_queries import SymbolHit, find_symbols_in_file_range, _row_to_symbol


@dataclass
class DiffHunk:
    """One unified-diff hunk in the *new* file coordinate system."""

    target_path: str
    source_path: str
    target_line_start: int  # inclusive, 1-based; 0 when the hunk has no new-file lines
    target_line_end: int  # inclusive
    source_line_start: int
    source_line_end: int
    source_length: int = 0
    target_length: int = 0


@dataclass
class ChangedSymbol:
    symbol_id: str
    fqn: str
    kind: str  # 'method' | 'type' | 'field'
    change_type: str  # 'added' | 'removed' | 'modified'
    file: str
    hunk_lines: list[int]
    cross_service_callers_count: int = 0


@dataclass
class PrRiskReport:
    changed_symbols: list[ChangedSymbol]
    blast_radius_total: int
    blast_radius_by_symbol: dict[str, int]
    cross_service_callers: int
    routes_touched: list[str]
    risk_score: float
    risk_band: str
    notes: list[str]


_BINARY_DIFF_LINE = re.compile(r"^Binary files .+ differ\s*$")
# Heuristic: new Java method/ctor-looking line. Covers annotations, method-level
# generics, `default` interface methods, and return types with spaces (e.g.
# `Map<String, String> m(`). Misses multi-line signatures, some compact record
# forms, and unusual annotations; `_notes_for_unindexed_additions` is best-effort.
_DECL_ADD = re.compile(
    r"^\+\s*"
    r"(?:(?:@[\w.]+\([^)]*\))\s+)*"
    r"(?:<[^>]+>\s+)?"
    r"(?:(?:public|private|protected|default|static|final|synchronized|abstract|native)\s+)*"
    r"(.+?)\s+(\w+)\s*\(",
)


def _strip_ab_prefix(path: str) -> str:
    p = path.strip()
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


def _hunk_ranges(h: Any) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return ((src_start, src_end inclusive), (tgt_start, tgt_end inclusive))."""
    src_len = int(getattr(h, "source_length", 0) or 0)
    tgt_len = int(getattr(h, "target_length", 0) or 0)
    src_start = int(getattr(h, "source_start", 0) or 0)
    tgt_start = int(getattr(h, "target_start", 0) or 0)
    if src_len <= 0:
        src_start, src_end = 0, 0
    else:
        src_end = src_start + src_len - 1
    if tgt_len <= 0:
        tgt_start, tgt_end = 0, 0
    else:
        tgt_end = tgt_start + tgt_len - 1
    return (src_start, src_end), (tgt_start, tgt_end)


def parse_unified_diff(diff_text: str) -> list[DiffHunk]:
    """Parse `diff_text` into logical hunks (non-binary, non-rename files only)."""
    if not (diff_text or "").strip():
        return []
    try:
        patches = PatchSet(diff_text.splitlines(keepends=True))
    except UnidiffParseError:
        return []
    out: list[DiffHunk] = []
    for pf in patches:
        if getattr(pf, "is_rename", False):
            continue
        tgt = _strip_ab_prefix(str(pf.path or ""))
        src = _strip_ab_prefix(str(getattr(pf, "source_file", "") or pf.path or ""))
        if not tgt:
            continue
        for h in pf:
            (s0, s1), (t0, t1) = _hunk_ranges(h)
            sl = int(getattr(h, "source_length", 0) or 0)
            tl = int(getattr(h, "target_length", 0) or 0)
            out.append(
                DiffHunk(
                    target_path=tgt,
                    source_path=src,
                    target_line_start=t0,
                    target_line_end=t1,
                    source_line_start=s0,
                    source_line_end=s1,
                    source_length=sl,
                    target_length=tl,
                )
            )
    return out


def collect_diff_file_notes(diff_text: str) -> list[str]:
    """Collect human-readable notes for binary diffs and renames (no crash)."""
    notes: list[str] = []
    if not (diff_text or "").strip():
        return notes
    for line in diff_text.splitlines():
        if _BINARY_DIFF_LINE.match(line):
            notes.append(f"skipped binary diff: {line.strip()}")
    try:
        patches = PatchSet(diff_text.splitlines(keepends=True))
    except UnidiffParseError:
        notes.append("diff text could not be fully parsed as a unified patch")
        return notes
    for pf in patches:
        if getattr(pf, "is_rename", False):
            a = _strip_ab_prefix(str(getattr(pf, "source_file", "") or ""))
            b = _strip_ab_prefix(str(pf.path or ""))
            notes.append(f"rename (symbols not mapped): {a} -> {b}")
    return notes


def _resolve_graph_filename(
    graph: Any,
    path: str,
    *,
    ambiguity_notes: list[str] | None = None,
) -> str | None:
    """Map a diff path to `Symbol.filename` values stored in Kuzu."""
    variants = {_strip_ab_prefix(path)}
    for v in list(variants):
        if v.startswith("./"):
            variants.add(v[2:])
    for candidate in variants:
        if not candidate:
            continue
        rows = graph._rows(
            "MATCH (s:Symbol) WHERE s.filename = $fn RETURN s.filename AS fn LIMIT 1",
            {"fn": candidate},
        )
        if rows and rows[0].get("fn"):
            return str(rows[0]["fn"])
    tail = path.strip().split("/")[-1]
    if tail:
        rows = graph._rows(
            "MATCH (s:Symbol) WHERE s.filename ENDS WITH $tail "
            "RETURN DISTINCT s.filename AS fn LIMIT 8",
            {"tail": "/" + tail},
        )
        n = len(rows)
        if n > 1 and ambiguity_notes is not None:
            fns = [str(r.get("fn") or "") for r in rows if r.get("fn")]
            ambiguity_notes.append(
                f"ambiguous filename tail {tail!r} ({n} graph paths); "
                f"ENDS WITH resolution skipped ({', '.join(fns[:4])}"
                f"{'…' if len(fns) > 4 else ''})",
            )
        if n == 1 and rows[0].get("fn"):
            return str(rows[0]["fn"])
    return None


def _symbol_to_changed(
    sym: SymbolHit,
    *,
    change_type: str,
    lines: list[int],
) -> ChangedSymbol:
    kind = sym.kind
    if kind in ("class", "interface", "enum", "record", "annotation"):
        mapped_kind = "type"
    elif kind == "field":
        mapped_kind = "field"
    elif kind == "constructor":
        mapped_kind = "method"
    else:
        mapped_kind = "method"
    uniq = sorted({int(x) for x in lines if int(x) > 0})
    return ChangedSymbol(
        symbol_id=sym.id,
        fqn=sym.fqn,
        kind=mapped_kind,
        change_type=change_type,
        file=sym.filename,
        hunk_lines=uniq,
    )


def _decl_added_lines_for_file(diff_text: str, resolved_filename: str) -> int:
    """Count `+` lines in the diff that look like Java member declarations for one file."""
    lines = diff_text.splitlines()
    in_file = False
    n = 0
    for line in lines:
        if line.startswith("+++ "):
            rest = line[4:].strip()
            if rest.startswith("b/"):
                rest = rest[2:]
            in_file = rest.endswith(resolved_filename) or resolved_filename.endswith(rest)
            continue
        if not in_file:
            continue
        if _DECL_ADD.match(line):
            n += 1
    return n


def _notes_for_unindexed_additions(
    graph: Any,
    diff_text: str,
    changed: list[ChangedSymbol],
    hunks: list[DiffHunk],
) -> list[str]:
    """Heuristic: added declaration lines vs indexed methods touched on the same file."""
    notes: list[str] = []
    if not diff_text.strip():
        return notes
    for h in hunks:
        tgt_fn = _resolve_graph_filename(graph, h.target_path)
        if not tgt_fn or h.target_line_start <= 0:
            continue
        decls = _decl_added_lines_for_file(diff_text, tgt_fn)
        if decls <= 0:
            continue
        methods_here = [c for c in changed if c.kind == "method" and c.file == tgt_fn]
        if decls > len(methods_here):
            extra = decls - len(methods_here)
            notes.append(
                f"{extra} new method(s) not yet indexed; risk underestimated",
            )
    return notes


def map_hunks_to_symbols(
    graph: Any,
    hunks: list[DiffHunk],
    *,
    path_ambiguity_notes: list[str] | None = None,
) -> list[ChangedSymbol]:
    """Map diff hunks to overlapping `Symbol` rows (graph-resident only)."""
    by_id: dict[str, ChangedSymbol] = {}

    def merge(sym: ChangedSymbol) -> None:
        existing = by_id.get(sym.symbol_id)
        if existing is None:
            by_id[sym.symbol_id] = sym
        else:
            if existing.change_type == "modified" or sym.change_type == "modified":
                ct = "modified"
            elif existing.change_type == "removed" or sym.change_type == "removed":
                ct = "removed"
            else:
                ct = sym.change_type
            merged_lines = sorted(set(existing.hunk_lines + sym.hunk_lines))
            by_id[sym.symbol_id] = ChangedSymbol(
                symbol_id=existing.symbol_id,
                fqn=existing.fqn,
                kind=existing.kind,
                change_type=ct,
                file=existing.file,
                hunk_lines=merged_lines,
            )

    for h in hunks:
        tgt_fn = _resolve_graph_filename(
            graph, h.target_path, ambiguity_notes=path_ambiguity_notes,
        )
        src_fn = (
            _resolve_graph_filename(
                graph, h.source_path, ambiguity_notes=path_ambiguity_notes,
            )
            if h.source_path
            else tgt_fn
        )
        if not tgt_fn and not src_fn:
            continue

        minus_only = h.target_length == 0 and h.source_length > 0

        # Removed lines on old file (process before modified so mixed hunks prefer modified)
        if h.source_line_start > 0 and h.source_line_end >= h.source_line_start and src_fn:
            rows = find_symbols_in_file_range(
                graph,
                filename=src_fn,
                start_line=h.source_line_start,
                end_line=h.source_line_end,
            )
            for sym in rows:
                if sym.kind == "file":
                    continue
                overlap = list(range(
                    max(h.source_line_start, sym.start_line),
                    min(h.source_line_end, sym.end_line) + 1,
                ))
                if minus_only:
                    merge(_symbol_to_changed(sym, change_type="removed", lines=overlap))

        # Modified / added lines on new file
        if h.target_line_start > 0 and h.target_line_end >= h.target_line_start and tgt_fn:
            rows = find_symbols_in_file_range(
                graph,
                filename=tgt_fn,
                start_line=h.target_line_start,
                end_line=h.target_line_end,
            )
            for sym in rows:
                if sym.kind == "file":
                    continue
                merge(_symbol_to_changed(sym, change_type="modified", lines=list(range(
                    max(h.target_line_start, sym.start_line),
                    min(h.target_line_end, sym.end_line) + 1,
                ))))

    return list(by_id.values())


def _impact_needle_for_changed(_graph: Any, fqn: str, mapped_kind: str) -> str:
    """Pick the `impact_analysis` needle: type FQN for members, else the symbol FQN."""
    if mapped_kind in ("method", "field", "constructor"):
        if "#" in fqn:
            return fqn.split("#", 1)[0]
    return fqn


def _is_public_interface_method(graph: Any, sym: SymbolHit) -> bool:
    if sym.kind != "method":
        return False
    if "private" in (sym.modifiers or []):
        return False
    type_fqn = sym.fqn.split("#", 1)[0] if "#" in sym.fqn else sym.fqn
    rows = graph._rows(
        "MATCH (t:Symbol) WHERE t.fqn = $f AND t.kind = 'interface' RETURN t.id LIMIT 1",
        {"f": type_fqn},
    )
    return bool(rows)


def _route_ids_for_symbol(graph: Any, symbol_id: str) -> list[str]:
    # Note: Kuzu rejects `ORDER BY r.id` together with `RETURN DISTINCT r.id` (binder loses `r`).
    q = (
        "MATCH (s:Symbol)-[e:EXPOSES]->(r:Route) WHERE s.id = $sid "
        "RETURN r.id AS id ORDER BY id"
    )
    seen: set[str] = set()
    out: list[str] = []
    for row in graph._rows(q, {"sid": symbol_id}):
        rid = str(row.get("id") or "")
        if rid and rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def compute_risk(graph: Any, changed: list[ChangedSymbol]) -> PrRiskReport:
    """Aggregate blast radius, routes, cross-service callers, and v1 risk score.

    Risk score stays in [0, 1]. Cross-service route callers add a bounded
    bump (up to +1.0) after normalization so they influence rank while
    preserving the public scalar contract.
    """
    notes: list[str] = []
    blast_by: dict[str, int] = {}
    blast_total = 0
    routes: list[str] = []
    cross_total = 0

    sym_cols = (
        "id", "kind", "name", "fqn", "package", "module", "microservice",
        "filename", "start_line", "end_line", "start_byte", "end_byte",
        "modifiers", "annotations", "capabilities", "role", "signature",
        "parent_id", "resolved",
    )
    _sym_return = ", ".join(f"s.{c} AS {c}" for c in sym_cols)

    iface_hit = 0.0
    enriched_changed: list[ChangedSymbol] = []
    for cs in changed:
        sym_row = graph._rows(
            "MATCH (s:Symbol) WHERE s.id = $id RETURN " + _sym_return,
            {"id": cs.symbol_id},
        )
        if not sym_row:
            continue
        row0 = sym_row[0]
        if iface_hit < 1.0:
            sym = _row_to_symbol(row0)
            if _is_public_interface_method(graph, sym):
                iface_hit = 1.0
        fqn = str(row0.get("fqn") or cs.fqn)
        needle = _impact_needle_for_changed(graph, fqn, cs.kind)
        ia = graph.impact_analysis(needle, depth=2, limit=400)
        n = len(ia)
        blast_by[cs.symbol_id] = n
        blast_total += n

        for e in graph.find_callers(cs.fqn, depth=2, limit=400):
            if (
                e.src.microservice
                and e.dst.microservice
                and e.src.microservice != e.dst.microservice
            ):
                cross_total += 1

        cs_cross_service = 0
        route_ids = _route_ids_for_symbol(graph, cs.symbol_id)
        for rid in route_ids:
            if rid not in routes:
                routes.append(rid)
            callers = graph._rows(
                "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid}) "
                "WHERE e.match = 'cross_service' "
                "RETURN c.id AS id LIMIT 500",
                {"rid": rid},
            )
            callers += graph._rows(
                "MATCH (s:Symbol)-[e:ASYNC_CALLS]->(r:Route {id: $rid}) "
                "WHERE e.match = 'cross_service' "
                "RETURN s.id AS id LIMIT 500",
                {"rid": rid},
            )
            cs_cross_service += len(callers)
        enriched_changed.append(
            ChangedSymbol(
                symbol_id=cs.symbol_id,
                fqn=cs.fqn,
                kind=cs.kind,
                change_type=cs.change_type,
                file=cs.file,
                hunk_lines=list(cs.hunk_lines),
                cross_service_callers_count=cs_cross_service,
            ),
        )

    def _normalize(x: float, ceiling: float) -> float:
        if ceiling <= 0:
            return 0.0
        return min(float(x), ceiling) / ceiling

    # v1 risk weights / ceilings (PR-B §1.2): intentionally simple baselines;
    # these constants are expected to be tuned after real-world use — do not treat as stable.
    w_blast, cap_blast = 0.4, 100.0
    w_cross, cap_cross = 0.3, 20.0
    w_iface = 0.2
    w_routes, cap_routes = 0.1, 5.0

    raw = (
        w_blast * _normalize(float(blast_total), cap_blast)
        + w_cross * _normalize(float(cross_total), cap_cross)
        + w_iface * iface_hit
        + w_routes * _normalize(float(len(routes)), cap_routes)
    )
    cross_service_bonus = min(
        5.0,
        float(sum(c.cross_service_callers_count for c in enriched_changed)),
    )
    score = max(0.0, min(1.0, raw + (cross_service_bonus / 5.0)))
    if score < 0.3:
        band = "low"
    elif score < 0.7:
        band = "medium"
    else:
        band = "high"

    return PrRiskReport(
        changed_symbols=list(enriched_changed),
        blast_radius_total=blast_total,
        blast_radius_by_symbol=blast_by,
        cross_service_callers=cross_total,
        routes_touched=routes,
        risk_score=score,
        risk_band=band,
        notes=notes,
    )


def pr_report_to_dict(rep: PrRiskReport) -> dict[str, Any]:
    return {
        "changed_symbols": [asdict(c) for c in rep.changed_symbols],
        "blast_radius_total": rep.blast_radius_total,
        "blast_radius_by_symbol": dict(rep.blast_radius_by_symbol),
        "cross_service_callers": rep.cross_service_callers,
        "routes_touched": list(rep.routes_touched),
        "risk_score": rep.risk_score,
        "risk_band": rep.risk_band,
        "notes": list(rep.notes),
    }


def analyze_pr_pipeline(graph: Any, diff_unified: str) -> PrRiskReport:
    """Full PR-B pipeline: parse → notes → map → risk."""
    notes = collect_diff_file_notes(diff_unified)
    hunks = parse_unified_diff(diff_unified)
    path_amb: list[str] = []
    changed = map_hunks_to_symbols(graph, hunks, path_ambiguity_notes=path_amb)
    notes.extend(path_amb)
    notes.extend(_notes_for_unindexed_additions(graph, diff_unified, changed, hunks))
    rep = compute_risk(graph, changed)
    merged = list(dict.fromkeys([*notes, *rep.notes]))
    return PrRiskReport(
        changed_symbols=rep.changed_symbols,
        blast_radius_total=rep.blast_radius_total,
        blast_radius_by_symbol=rep.blast_radius_by_symbol,
        cross_service_callers=rep.cross_service_callers,
        routes_touched=rep.routes_touched,
        risk_score=rep.risk_score,
        risk_band=rep.risk_band,
        notes=merged,
    )
