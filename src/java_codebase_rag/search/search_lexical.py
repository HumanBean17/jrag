#!/usr/bin/env python3
"""Lexical (keyword) search over the LadybugDB symbol graph.

Graph-only fallback for the `search` tool on macOS Intel installs, where the
vector stack (lancedb / torch / sentence-transformers) is unavailable (see the
PEP 508 markers in pyproject.toml). Returns row-dicts in the SAME shape as
`search_lancedb.run_search`, so `mcp_v2._row_to_search_hit` and the rest of
`search_v2` work unchanged — `search` simply ranks by keyword relevance instead
of embeddings, with an advisory noting the mode.

This module imports only LadybugDB (always installed) and `search_scoring`
(dependency-free). It MUST NOT import lancedb/torch, and it MUST NOT import
`mcp_v2` (circular: mcp_v2 dispatches to this module). The NodeFilter is
duck-typed; `_lexical_where` mirrors `mcp_v2._symbol_where_from_filter` and is
guarded by a parity unit test.
"""

from __future__ import annotations

import os
import weakref
from pathlib import Path
from typing import TYPE_CHECKING, Any

from java_codebase_rag.graph.ladybug_queries import LadybugGraph
from java_codebase_rag.search.search_scoring import (
    SYMBOL_FTS_INDEX,
    _ROLE_SCORE_WEIGHTS,
    _TYPE_MATCH_BONUS_CAP,
    _TYPE_MATCH_BONUS_PER_HIT,
    _clamp01,
    _dedup_by_fqn,
    _query_tokens,
    _split_identifier,
)

if TYPE_CHECKING:
    from java_codebase_rag.mcp.mcp_v2 import NodeFilter

# Lexical relevance weights. The class/file name is the strongest discovery
# signal (mirrors the type-name bonus rationale in search_scoring); fqn/package
# overlap is next; signature/annotation/capability text is a weaker corroborator.
_NAME_MATCH_WEIGHT = 0.45
_FQN_MATCH_WEIGHT = 0.20
_TEXT_MATCH_WEIGHT = 0.15

# Display-score normalization denominator. Mirrors search_scoring._HYBRID_SCORE_MAX
# discipline: sum of each additive component's maximum so the displayed score is
# rank-monotonic in [0, 1]. (= 0.45 + 0.10 + 0.20 + 0.15 + 0.10 = 1.00)
LEXICAL_SCORE_MAX = (
    _NAME_MATCH_WEIGHT
    + _TYPE_MATCH_BONUS_CAP
    + _FQN_MATCH_WEIGHT
    + _TEXT_MATCH_WEIGHT
    + max(_ROLE_SCORE_WEIGHTS.values())
)

_SNIPPET_MAX_LINES = 20
_SNIPPET_MAX_CHARS = 800
# Safety bound on the candidate fetch (full scan over Symbols; bounded so huge
# repos don't pull unbounded rows into Python).
_CANDIDATE_LIMIT_CAP = 5000

_SYMBOL_RETURN = (
    "s.id AS id, s.kind AS kind, s.name AS name, s.fqn AS fqn, "
    "s.package AS package, s.module AS module, s.microservice AS microservice, "
    "s.filename AS filename, s.start_line AS start_line, s.end_line AS end_line, "
    "s.start_byte AS start_byte, s.end_byte AS end_byte, "
    "s.annotations AS annotations, s.capabilities AS capabilities, "
    "s.role AS role, s.signature AS signature, s.parent_id AS parent_id"
)


def _lexical_where(f: Any, *, path_contains: str | None) -> tuple[str, dict[str, Any]]:
    """Cypher WHERE for Symbol nodes from a NodeFilter (+ path_contains pushdown).

    Mirrors ``mcp_v2._symbol_where_from_filter``; kept local so this module stays
    import-isolated from mcp_v2 (and unit-testable standalone). ``path_contains``
    is pushed down into Cypher because ``search_v2`` only re-filters the windowed
    page post-fetch — without pushdown a path filter could empty the page even
    when deeper-ranked rows match. A parity unit test guards drift.
    """
    preds: list[str] = []
    params: dict[str, Any] = {}
    if f is not None:
        if getattr(f, "microservice", None):
            preds.append("s.microservice = $microservice")
            params["microservice"] = f.microservice
        if getattr(f, "module", None):
            preds.append("s.module = $module")
            params["module"] = f.module
        if getattr(f, "role", None):
            preds.append("s.role = $role")
            params["role"] = f.role
        if getattr(f, "exclude_roles", None):
            preds.append("NOT s.role IN $exclude_roles")
            params["exclude_roles"] = list(f.exclude_roles)
        if getattr(f, "generated_only", False):
            preds.append("s.generated = true")
        if getattr(f, "exclude_generated", False):
            preds.append("(s.generated IS NULL OR s.generated = false)")
        if getattr(f, "annotation", None):
            preds.append("list_contains(s.annotations, $annotation)")
            params["annotation"] = f.annotation
        if getattr(f, "capability", None):
            preds.append("$capability IN s.capabilities")
            params["capability"] = f.capability
        if getattr(f, "fqn_contains", None):
            preds.append("s.fqn CONTAINS $fqn_contains")
            params["fqn_contains"] = f.fqn_contains
        if getattr(f, "symbol_kind", None):
            preds.append("s.kind = $symbol_kind")
            params["symbol_kind"] = f.symbol_kind
        if getattr(f, "symbol_kinds", None):
            preds.append("s.kind IN $symbol_kinds")
            params["symbol_kinds"] = list(f.symbol_kinds)
    if path_contains:
        preds.append("s.filename CONTAINS $path_contains")
        params["path_contains"] = path_contains
    where = f"WHERE {' AND '.join(preds)}" if preds else ""
    return where, params


def _enclosing_type_fqn(fqn: str) -> str:
    """Member fqn ``{parent_fqn}#{signature}`` -> parent type fqn; type fqn (no '#') unchanged."""
    return fqn.split("#", 1)[0] if fqn else fqn


def _resolve_source_root(graph: LadybugGraph) -> str:
    """Authoritative source root is the one cached on the graph at index time."""
    try:
        root = str(graph.meta().get("source_root") or "")
    except Exception:
        root = ""
    return root or os.environ.get("JAVA_CODEBASE_RAG_SOURCE_ROOT", "").strip()


def _read_snippet(
    source_root: str, filename: str, start_line: Any, end_line: Any, signature: str, fqn: str
) -> str:
    """Real source snippet for [start_line, end_line] from disk (capped); synthesized
    from `signature` (fallback `fqn`) on any failure."""
    synth = (signature or "").strip() or fqn
    try:
        sl = int(start_line) if start_line else 0
        el = int(end_line) if end_line else sl
    except (TypeError, ValueError):
        return synth
    if sl <= 0 or not filename:
        return synth
    try:
        p = Path(filename)
        full = p if p.is_absolute() else (Path(source_root) / filename)
        if not p.is_absolute() and not source_root:
            return synth
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return synth
    lines = text.splitlines()
    lo = max(sl - 1, 0)
    hi = min(el if el >= sl else sl, lo + _SNIPPET_MAX_LINES)
    chunk = "\n".join(lines[lo:hi]).strip()
    if len(chunk) > _SNIPPET_MAX_CHARS:
        chunk = chunk[: _SNIPPET_MAX_CHARS - 1] + "…"
    return chunk or synth


def _token_overlap(haystack_toks: set[str], needle_toks: set[str]) -> float:
    """Fraction of needle tokens present in haystack (0..1)."""
    if not needle_toks:
        return 0.0
    return len(needle_toks & haystack_toks) / len(needle_toks)


# BM25 candidate fetch via the LadybugDB FTS index (fork A). DB-side indexed ranking
# replaces the heuristic's bounded Python scan; the heuristic below still scores the
# fetched candidates (name/type/fqn/role) and is the fallback when the FTS index or
# extension is unavailable (older graph, offline first run).
_FTS_CANDIDATE_K = 200  # top-K BM25 candidates; re-filtered by NodeFilter before ranking
# Connections that have run LOAD EXTENSION FTS. Keyed by the connection OBJECT (WeakSet),
# NOT id() — id() is reused after GC, which would let a fresh connection skip LOAD and then
# fail at QUERY_FTS_INDEX under test batching. Entries die with the connection.
_FTS_LOADED_CONNS: "weakref.WeakSet[object]" = weakref.WeakSet()


def _ensure_fts_loaded(g: LadybugGraph) -> bool:
    """LOAD EXTENSION FTS on the graph's (read-only) connection, once per connection.

    Returns False if the extension can't be loaded (absent / offline) so the caller
    falls back to the heuristic scan.
    """
    conn = g._conn  # noqa: SLF001
    try:
        if conn in _FTS_LOADED_CONNS:
            return True
    except Exception:  # connection not weakref-able → LOAD every call (correct, slow)
        pass
    try:
        g._rows("LOAD EXTENSION FTS")  # noqa: SLF001
        try:
            _FTS_LOADED_CONNS.add(conn)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _try_fts_candidates(
    g: LadybugGraph,
    query: str,
    filter: NodeFilter | None,
    path_contains: str | None,
) -> dict | None:
    """Fetch BM25-ranked Symbol candidates via the FTS index; re-apply NodeFilter.

    Returns ``{"rows": [...], "scores": {id: bm25}}`` (rows are the same shape the
    heuristic scan yields), or ``None`` when FTS is unavailable (extension won't load,
    or the index isn't present on this graph) so the caller falls back.

    Two-step: (1) ``QUERY_FTS_INDEX`` returns the top-K node ids by Okapi BM25 over
    ``Symbol.search_text``; (2) re-MATCH those ids with the full ``_lexical_where``
    predicates (role / module / path / kind≠file,package) so the filter logic stays
    defined in one place. ``search_text`` is built at index time by ``build_ast_graph``
    from the same ``_split_identifier`` the re-rank below uses, so index- and query-time
    tokenization agree.
    """
    if not _ensure_fts_loaded(g):
        return None
    idx_rows = g._rows("CALL SHOW_INDEXES() RETURN index_name")  # noqa: SLF001
    names = {row.get("index_name") for row in idx_rows}
    if SYMBOL_FTS_INDEX not in names:
        return None
    fts = g._rows(  # noqa: SLF001
        f"CALL QUERY_FTS_INDEX('Symbol', '{SYMBOL_FTS_INDEX}', $q, top := $k) "
        "RETURN node.id AS id, score",
        {"q": query, "k": _FTS_CANDIDATE_K},
    )
    if not fts:
        return {"rows": [], "scores": {}}
    scores = {row["id"]: float(row.get("score") or 0.0) for row in fts}
    ids = list(scores.keys())

    # Re-MATCH the K ids with the SAME predicates the heuristic pushes down, so
    # NodeFilter / path / structural-kind filtering is defined exactly once.
    where, params = _lexical_where(filter, path_contains=path_contains)
    struct_pred = "(s.kind <> 'file' AND s.kind <> 'package')"
    if not where:
        where = f"WHERE s.id IN $ids AND {struct_pred}"
    else:
        where = where.replace("WHERE ", f"WHERE s.id IN $ids AND {struct_pred} AND ", 1)
    params["ids"] = ids
    rows = g._rows(f"MATCH (s:Symbol) {where} RETURN {_SYMBOL_RETURN}", params)  # noqa: SLF001
    return {"rows": rows, "scores": scores}


def run_lexical_search(
    query: str,
    *,
    table: str = "java",
    limit: int = 5,
    offset: int = 0,
    path_contains: str | None = None,
    filter: NodeFilter | None = None,
    explain: bool = False,
    dedup: bool = True,
    advisories: list[str] | None = None,
    graph: LadybugGraph | None = None,
) -> list[dict]:
    """Keyword search over Symbol nodes; returns ``run_search``-shaped row-dicts.

    BM25-first (fork A): when the LadybugDB ``sym_fts`` index exists, candidates are
    fetched DB-side via Okapi BM25 over ``Symbol.search_text`` (killing the bounded
    Python scan that silently missed matches past the cap on large repos) and then
    re-ranked here by the name/type/fqn/role heuristic. Falls back to that heuristic
    scan when the FTS index or extension is unavailable (older graph, offline first run).

    Raises ``RuntimeError`` (message contains "lexical search unavailable") if no
    symbol graph exists — the caller maps that to a clean failure envelope. Returns
    ``[]`` for ``table in ("sql", "yaml")`` (those LanceDB tables aren't built in
    graph-only mode) and when the graph exists but nothing matches.
    """
    # sql/yaml LanceDB tables don't exist in graph-only mode.
    if table in ("sql", "yaml"):
        return []

    if graph is None and not LadybugGraph.exists():
        raise RuntimeError(
            "lexical search unavailable: no symbol graph found; "
            "run `java-codebase-rag init` or `java-codebase-rag reprocess` to build one"
        )
    g = graph or LadybugGraph.get()

    # --- candidate fetch: BM25 (FTS) preferred, heuristic scan fallback ---
    bm25_scores: dict[str, float] = {}
    use_fts = False
    fts = _try_fts_candidates(g, query, filter, path_contains)
    if fts is not None:
        rows = fts["rows"]
        bm25_scores = fts["scores"]
        use_fts = True
    else:
        where, params = _lexical_where(filter, path_contains=path_contains)
        # Always exclude structural Symbol nodes. Files and packages are :Symbol-labeled
        # (kind='file'/'package') but aren't searchable code declarations — without this
        # a token that appears in a filename (e.g. 'distribution' in
        # 'DistributionChunkService.java') would surface the file node as a hit.
        struct_pred = "(s.kind <> 'file' AND s.kind <> 'package')"
        where = f"WHERE {struct_pred}" if not where else where.replace("WHERE ", f"WHERE {struct_pred} AND ", 1)
        # The heuristic scan returns rows in storage order — there is NO DB-side relevance
        # ORDER BY without the FTS index — so fetch the FULL candidate pool up to the safety
        # cap and rank here. A pagination-derived LIMIT (4x the page) is correct on the
        # vector path where LanceDB returns rows pre-ranked, but on this unordered scan it
        # would return only the first ~N symbols in arbitrary storage order and silently
        # miss the best match on any non-trivial repo. The BM25 (FTS) path above has no cap.
        params["lim"] = _CANDIDATE_LIMIT_CAP
        cypher = f"MATCH (s:Symbol) {where} RETURN {_SYMBOL_RETURN} LIMIT $lim"
        rows = g._rows(cypher, params)  # noqa: SLF001 — de facto public read API (see find_v2)
        # If the fetch hit the safety cap, deeper matches were never ranked. Surface it so
        # a user on a large repo isn't silently shown an incomplete result set.
        if advisories is not None and len(rows) >= _CANDIDATE_LIMIT_CAP:
            advisories.append(
                f"lexical search scanned the first {_CANDIDATE_LIMIT_CAP} matching symbols "
                "(repo cap); deeper matches were not ranked — refine the query or add a filter"
            )

    query_toks = _query_tokens(query)
    source_root = _resolve_source_root(g)
    role_locked = bool(
        filter and (getattr(filter, "role", None) or getattr(filter, "exclude_roles", None))
    )

    out: list[dict] = []
    for r in rows:
        name = str(r.get("name") or "")
        fqn = str(r.get("fqn") or "")
        type_fqn = _enclosing_type_fqn(fqn)
        sig = str(r.get("signature") or "")
        anns = list(r.get("annotations") or [])
        caps = list(r.get("capabilities") or [])
        role_raw = str(r.get("role") or "")

        name_toks = set(_split_identifier(name))
        type_toks = set(_split_identifier(type_fqn.rsplit(".", 1)[-1]))
        fqn_toks = set(_split_identifier(fqn))
        sig_toks = _query_tokens(
            " ".join([sig, " ".join(anns), " ".join(caps), str(r.get("package") or "")])
        )

        name_overlap = len(query_toks & name_toks)
        name_match = (
            1.0
            if (query_toks and query_toks <= name_toks)
            else (min(name_overlap / max(len(name_toks), 1), 1.0) if name_toks else 0.0)
        )
        type_hits = len(query_toks & type_toks)
        type_match = min(type_hits * _TYPE_MATCH_BONUS_PER_HIT, _TYPE_MATCH_BONUS_CAP)
        fqn_match = _token_overlap(fqn_toks, query_toks)
        text_overlap = _token_overlap(sig_toks, query_toks)
        text_match = text_overlap * _TEXT_MATCH_WEIGHT

        # A keyword search must require at least one lexical hit — role alone never
        # qualifies a row (it only boosts/reorders matches). On the BM25 path the FTS
        # index already established textual relevance, so the qualifier is heuristic-only.
        # Degenerate queries with no usable tokens fall through to role-ranked listing.
        if query_toks and not use_fts and not (name_overlap or type_hits or fqn_match or text_overlap):
            continue

        role_w = 0.0 if role_locked else _ROLE_SCORE_WEIGHTS.get(role_raw.upper(), 0.0)

        if query_toks:
            raw = (
                _NAME_MATCH_WEIGHT * name_match
                + type_match
                + _FQN_MATCH_WEIGHT * fqn_match
                + text_match
                + role_w
            )
        else:
            raw = role_w  # degenerate query: rank by role only
        score = _clamp01(raw / LEXICAL_SCORE_MAX)

        comps = {
            "name_match": round(name_match, 4),
            "type_match": round(type_match, 4),
            "fqn_match": round(fqn_match, 4),
            "lexical_relevance": round(raw, 4),
            "role_weight": role_w,
        }
        if use_fts:
            comps["bm25"] = round(float(bm25_scores.get(r.get("id"), 0.0)), 4)

        sl, el, sb, eb = r.get("start_line"), r.get("end_line"), r.get("start_byte"), r.get("end_byte")
        out.append(
            {
                "_score": score,
                "_kind": "java",
                "_score_components": comps,
                "filename": str(r.get("filename") or ""),
                "text": _read_snippet(source_root, str(r.get("filename") or ""), sl, el, sig, fqn),
                "primary_type_fqn": type_fqn or None,
                # Raw node fqn (members are 'Type#method(...)') feeds
                # _node_matches_filter's fqn_contains re-check (mcp_v2.py). Without it the
                # post-filter falls back to primary_type_fqn (the bare type) and drops
                # member-level matches the Cypher pushdown already accepted.
                "fqn": fqn,
                "microservice": r.get("microservice"),
                "module": r.get("module"),
                "role": role_raw or None,
                "kind": r.get("kind"),
                "symbol_id": r.get("id"),
                "annotations": anns,
                "capabilities": caps,
                "start": {
                    "line": int(sl) if sl is not None else None,
                    "byte_offset": int(sb) if sb is not None else 0,
                },
                "end": {
                    "line": int(el) if el is not None else None,
                    "byte_offset": int(eb) if eb is not None else 0,
                },
            }
        )

    out.sort(key=lambda d: float(d.get("_score", 0.0)), reverse=True)
    out = _dedup_by_fqn(out, dedup_by_fqn=dedup)
    return out[offset : offset + limit]
