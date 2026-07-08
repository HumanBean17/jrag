"""Stateless absence diagnosis (PR-ABS-2) — the feature's core.

``diagnose(...)`` is the single place empty-MCP-result logic lives. It classifies
an empty exploration result by cause and emits cause-specific help. Pure function
of its inputs (incl. the :class:`VocabularyIndex`); no I/O, no mutation. Consumed
by PR-ABS-3 (MCP wiring) and PR-ABS-4 (CLI).

Similarity metric
-----------------
Identifier did-you-mean uses ``difflib.SequenceMatcher(None, a, b).ratio()``
(stdlib, ∈ [0,1]) on the query's normalized name vs each candidate's
``normalized_name``; ``distance = 1.0 - similarity``. ``difflib`` is stdlib and
adequate for identifier typo/misremember detection — Jaro-Winkler would be
marginally better but is not stdlib and not worth a dependency. See the task
brief's resolution 1.

Conservative absence
--------------------
False-absent (declaring a real symbol absent) is the catastrophic failure mode.
The two-band threshold policy defaults the middle band to ``refine_query`` and
commits to ``not_in_project`` only when best similarity < ``absence_absent_floor``
AND the query is identifier-shaped. ``closest_symbols``/``distances`` are ALWAYS
populated regardless of verdict.
"""

from __future__ import annotations

import logging
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Literal

from absence_types import (
    AbsenceDiagnosis,
    AbsenceProof,
    ExternalIdentity,
    FilterRelaxation,
    FilterRelaxationDim,
    VocabularyContext,
)
from absence_vocab import SymbolRecord, VocabularyIndex, _normalize_name
from graph_types import NodeRef
from mcp_hints import _IDENTIFIER_FILTER_FIELDS

log = logging.getLogger(__name__)

__all__ = ["diagnose"]

# Dimensions whose relaxation _filter_relaxation can probe on the graph. These
# mirror the single-dim filters handled by ``_zero_result_guidance`` (jrag.py).
_RELAXABLE_DIMS: tuple[str, ...] = ("role", "microservice", "module")

# Node-id prefixes (graph_types._node_kind_from_id). Used to tell a describe
# node_id miss apart from an FQN lookup.
_NODE_ID_PREFIXES: tuple[str, ...] = (
    "ucs:", "sym:", "route:", "r:", "client:", "c:", "producer:", "p:",
)

# Small English stopword set; a single stopword is treated as NL, not identifier.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "with", "on", "at",
    "by", "is", "are", "be", "was", "were", "how", "does", "do", "what", "where",
    "which", "who", "why", "when", "find", "show", "get", "list", "all", "any",
    "this", "that", "these", "those", "from", "into", "use", "using", "used",
})


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #


def diagnose(
    *,
    tool: Literal["search", "find", "neighbors", "describe", "resolve"],
    query: str | None,
    filt: dict | None,                 # find's model_dump'd filter
    filter_kind: str | None,           # find's kind, for identifier-shape test
    root_node: NodeRef | None,         # neighbors/describe subject
    scope: dict[str, str],             # {"microservice":..,"module":..}
    vocab: VocabularyIndex,
    graph: Any,                        # LadybugGraph
    cfg: Any,                          # ResolvedOperatorConfig (thresholds)
) -> AbsenceDiagnosis | None:
    """Classify an empty result and emit cause-specific help.

    Returns ``None`` when the master toggle is off or on unrecoverable error.
    Never raises: any exception is logged and degrades to a minimal
    ``refine_query`` (or ``None`` if even that cannot be built).
    """
    try:
        if not getattr(cfg, "absence_diag_enabled", True):
            return None
        return _diagnose_inner(
            tool=tool,
            query=query,
            filt=filt,
            filter_kind=filter_kind,
            root_node=root_node,
            scope=scope,
            vocab=vocab,
            graph=graph,
            cfg=cfg,
        )
    except Exception:  # noqa: BLE001 — diagnosis must never fail the tool
        log.exception("absence diagnosis failed; degrading to refine_query")
        return _fallback_refine()


# --------------------------------------------------------------------------- #
# Decision procedure                                                          #
# --------------------------------------------------------------------------- #


def _diagnose_inner(
    *,
    tool: str,
    query: str | None,
    filt: dict | None,
    filter_kind: str | None,
    root_node: NodeRef | None,
    scope: dict[str, str],
    vocab: VocabularyIndex,
    graph: Any,
    cfg: Any,
) -> AbsenceDiagnosis | None:
    # --- External-wins: emit external_dependency first for any external target.
    ext = _detect_external(query, filt, filter_kind, root_node, vocab)
    if ext is not None:
        return AbsenceDiagnosis(
            verdict="external_dependency",
            cause="external",
            message=(
                f"`{ext.fqn}` is referenced by this project but not defined in it "
                f"({ext.reason}). It is an external dependency."
            ),
            external_identity=ext,
        )

    # --- neighbors/describe subject present.
    if root_node is not None:
        return _diagnose_neighbors(root_node, graph)

    # --- describe by node_id (not fqn): an unknown id, not a misspelled name.
    if tool == "describe" and query and _looks_like_node_id(query):
        return AbsenceDiagnosis(
            verdict="refine_query",
            cause="identifier_miss",
            message=(
                f"No node with id `{query}`. Run `resolve` to map a name/FQN to an id, "
                "or `search` to discover symbols."
            ),
        )

    # --- find (filter) path.
    if filt is not None and filter_kind is not None:
        return _diagnose_find(filt, filter_kind, scope, vocab, graph, cfg)

    # --- search/resolve/describe-by-fqn (query) path.
    if query:
        return _diagnose_query(query, vocab, graph, cfg)

    # Nothing to classify on (no query, no filt, no root_node). Be conservative.
    return _fallback_refine()


def _diagnose_query(
    query: str, vocab: VocabularyIndex, graph: Any, cfg: Any,
) -> AbsenceDiagnosis:
    # Empty vocab guard: never declare not_in_project on an unindexed/empty graph
    if vocab.symbol_count == 0:
        return AbsenceDiagnosis(
            verdict="refine_query",
            cause="identifier_miss",
            message=(
                "Index appears empty/unindexed — verify the project was indexed "
                "before concluding a symbol is absent."
            ),
        )

    if _is_identifier_shaped(query):
        closest, distances, best_sim = _did_you_mean(query, vocab, cfg)
        verdict, cause, proof = _threshold_verdict(best_sim, cfg, identifier=True)
        # False-absent guard: if the query exactly resolves to a real project
        # symbol (simple name OR FQN, case-insensitive), never declare not_in_project.
        if verdict == "not_in_project" and _exact_symbol_exists(query, vocab):
            verdict, proof = "refine_query", None
        if proof is not None:
            proof.symbol_count_scanned = vocab.symbol_count
        message = _identifier_message(query, verdict, closest)
        return AbsenceDiagnosis(
            verdict=verdict,
            cause=cause,
            message=message,
            closest_symbols=closest,
            distances=distances,
            proof=proof,
        )
    # Natural language → assemble vocabulary context, no did-you-mean.
    ctx = _build_vocabulary_context(graph, vocab)
    return AbsenceDiagnosis(
        verdict="refine_query",
        cause="nl_miss",
        message=(
            f"No symbol matches `{query}`. Refine the query — try an identifier "
            "(class/method/FQN) or browse the project vocabulary below."
        ),
        vocabulary_context=ctx,
    )


def _diagnose_find(
    filt: dict,
    filter_kind: str,
    scope: dict[str, str],
    vocab: VocabularyIndex,
    graph: Any,
    cfg: Any,
) -> AbsenceDiagnosis:
    # Empty vocab guard: never declare not_in_project on an unindexed/empty graph
    if vocab.symbol_count == 0:
        return AbsenceDiagnosis(
            verdict="refine_query",
            cause="identifier_miss",
            message=(
                "Index appears empty/unindexed — verify the project was indexed "
                "before concluding a symbol is absent."
            ),
        )

    identifier = _extract_identifier(filt, filter_kind)

    if identifier is not None:
        # Identifier-shaped filter: run did-you-mean on the identifier value.
        closest, distances, best_sim = _did_you_mean(identifier, vocab, cfg)
        if best_sim >= cfg.absence_close_threshold:
            # Close hit exists → the filter (or scope) excluded it. Show where it lives.
            relax = _filter_relaxation(filt, filter_kind, scope, graph, identifier)
            return AbsenceDiagnosis(
                verdict="refine_query",
                cause="filter_miss",
                message=(
                    f"No results for `{identifier}` under the current filter. "
                    "Close matches exist — try relaxing a dimension (see filter_relaxation)."
                ),
                closest_symbols=closest,
                distances=distances,
                filter_relaxation=relax,
            )
        # No close hit → identifier miss; apply the conservative threshold.
        verdict, cause, proof = _threshold_verdict(best_sim, cfg, identifier=True)
        if verdict == "not_in_project" and _exact_symbol_exists(identifier, vocab):
            verdict, proof = "refine_query", None
        if proof is not None:
            proof.symbol_count_scanned = vocab.symbol_count
        return AbsenceDiagnosis(
            verdict=verdict,
            cause=cause,
            message=_identifier_message(identifier, verdict, closest),
            closest_symbols=closest,
            distances=distances,
            proof=proof,
        )

    # Broad / non-identifier filter → filter_miss with relaxation suggestions.
    relax = _filter_relaxation(filt, filter_kind, scope, graph, None)
    return AbsenceDiagnosis(
        verdict="refine_query",
        cause="filter_miss",
        message=(
            "No results under the current filter. Matches exist under other values "
            "(see filter_relaxation)."
        ),
        filter_relaxation=relax,
    )


def _diagnose_neighbors(root_node: NodeRef, graph: Any) -> AbsenceDiagnosis:
    if _neighbors_meaningful_empty(root_node, graph):
        return AbsenceDiagnosis(
            verdict="correct_empty",
            cause="meaningful_empty",
            message=(
                f"`{root_node.fqn or root_node.id}` has no neighbors of the requested "
                "type here — this is a genuine leaf / external entrypoint, not an error."
            ),
        )
    return AbsenceDiagnosis(
        verdict="refine_query",
        cause="identifier_miss",
        message=(
            f"No neighbors for `{root_node.fqn or root_node.id}` with the requested "
            "edge type/direction. Run `describe` and inspect `edge_summary` for the "
            "edge types this node actually participates in."
        ),
    )


# --------------------------------------------------------------------------- #
# Did-you-mean + thresholds                                                    #
# --------------------------------------------------------------------------- #


def _did_you_mean(
    identifier: str, vocab: VocabularyIndex, cfg: Any,
) -> tuple[list[NodeRef], list[float], float]:
    """Rank vocabulary candidates by SequenceMatcher similarity to ``identifier``.

    Returns (closest_symbols, distances, best_similarity). When n-gram lookup
    yields no candidates (no q-gram overlap at all), falls back to a bounded
    linear scan over all records so ``closest_symbols`` is still populated —
    this backs the "nearest-by-name" guarantee on the ``not_in_project`` path.
    """
    limit = int(getattr(cfg, "absence_candidate_count", 5))
    query_norm = _normalize_name(identifier)

    candidates = vocab.lookup(identifier, limit=limit)
    if not candidates and vocab.records:
        # Rare: totally novel token with zero q-gram overlap. Bounded scan for
        # the nearest-by-name records so not_in_project still shows nearest names.
        candidates = vocab.records

    scored: list[tuple[SymbolRecord, float]] = [
        (rec, _similarity(query_norm, rec.normalized_name)) for rec in candidates
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    top = scored[:limit]
    closest = [_build_node_ref(rec) for rec, _ in top]
    distances = [round(1.0 - sim, 4) for _, sim in top]
    best_sim = top[0][1] if top else 0.0
    return closest, distances, best_sim


def _threshold_verdict(
    best_sim: float, cfg: Any, *, identifier: bool,
) -> tuple[str, str, AbsenceProof | None]:
    """Conservative two-band threshold policy.

    Returns (verdict, cause, proof). Middle band defaults to ``refine_query``;
    ``not_in_project`` only when best similarity < ``absence_absent_floor`` AND
    the query is identifier-shaped.
    """
    close = float(getattr(cfg, "absence_close_threshold", 0.85))
    floor = float(getattr(cfg, "absence_absent_floor", 0.40))
    if best_sim < floor and identifier:
        proof = AbsenceProof(
            nearest_distance=round(1.0 - best_sim, 4),
            symbol_count_scanned=0,  # filled by caller via vocab
            thresholds_applied={"absence_close_threshold": close, "absence_absent_floor": floor},
            query_shape="identifier",
        )
        return "not_in_project", "identifier_miss", proof
    # close band OR middle band → refine_query (conservative; never false-absent).
    return "refine_query", "identifier_miss", None


def _identifier_message(
    query: str, verdict: str, closest: list[NodeRef],
) -> str:
    if verdict == "not_in_project":
        return (
            f"No symbol matching `{query}` was found in the project vocabulary. "
            "It does not appear to be defined here."
        )
    if closest:
        names = ", ".join(s.name or s.fqn for s in closest[:3])
        return (
            f"No exact match for `{query}`. Closest symbols: {names}. "
            "Refine the query (typo? scope?) and retry."
        )
    return f"No match for `{query}`. Refine the query and retry."


# --------------------------------------------------------------------------- #
# External detection                                                          #
# --------------------------------------------------------------------------- #


def _detect_external(
    query: str | None,
    filt: dict | None,
    filter_kind: str | None,
    root_node: NodeRef | None,
    vocab: VocabularyIndex,
) -> ExternalIdentity | None:
    """External-wins: if the target is external/phantom, emit its identity first."""
    # root_node (neighbors/describe subject).
    if root_node is not None:
        if root_node.kind == "unresolved_call_site":
            return ExternalIdentity(fqn=root_node.fqn, reason="unresolved-call")
        if root_node.fqn:
            ext = _external_identity_for(root_node.fqn, vocab)
            if ext is not None:
                return ext

    # free-text query (search/resolve/describe-by-fqn).
    if query:
        ext = _external_identity_for(query, vocab)
        if ext is not None:
            return ext

    # find identifier filter value.
    identifier = _extract_identifier(filt, filter_kind) if filt is not None else None
    if identifier:
        ext = _external_identity_for(identifier, vocab)
        if ext is not None:
            return ext

    return None


def _external_identity_for(name: str, vocab: VocabularyIndex) -> ExternalIdentity | None:
    is_ext, reason = vocab.is_external(name)
    if is_ext and reason in ("prefix", "phantom"):
        # Prefer the corpus FQN when we can resolve it (richer than the bare query).
        fqn = name
        for rec in vocab.records:
            if rec.simple_name == name or rec.fqn == name:
                fqn = rec.fqn or name
                break
        return ExternalIdentity(fqn=fqn, reason=reason)
    return None


# --------------------------------------------------------------------------- #
# Neighbors meaningful-empty detection                                        #
# --------------------------------------------------------------------------- #


def _neighbors_meaningful_empty(root_node: NodeRef, graph: Any) -> bool:
    """A genuine leaf or external entrypoint → ``correct_empty``.

    Reuses the conditions behind ``is_external_entrypoint`` (jrag.py:2452): an
    HTTP ``http_endpoint`` route with inbound handlers is an external entrypoint,
    so zero callers is meaningful. A symbol with no edges at all is an isolated
    leaf. Everything else (has edges, just not the requested type) → refine.

    Kafka topics are not considered external entrypoints: their empty-callers
    semantics differ from HTTP routes (per is_external_entrypoint precedent).
    """
    try:
        if root_node.kind == "route":
            # Fetch the route's kind property (http_endpoint vs kafka_topic).
            kind_rows = graph._rows(
                "MATCH (r:Route) WHERE r.id = $id RETURN r.kind AS k",
                {"id": root_node.id},
            )
            route_kind = kind_rows[0].get("k") if kind_rows else ""
            # Only http_endpoint routes with handlers are external entrypoints.
            if route_kind == "http_endpoint":
                handlers = graph.find_route_handlers(route_id=root_node.id)
                if handlers:
                    return True
            # kafka_topic routes and handler-less routes are NOT meaningful empty.
            return False
        # Symbol/other: meaningful empty only if it has zero edges (isolated leaf).
        rows = graph._rows(  # noqa: SLF001 - same pattern as graph_types helpers
            "MATCH (n)--(m) WHERE n.id = $id RETURN count(*) AS c",
            {"id": root_node.id},
        )
        if rows:
            return int(rows[0].get("c") or 0) == 0
    except Exception:  # noqa: BLE001 - degrade to refine_query on graph error
        log.debug("neighbors meaningful-empty probe failed", exc_info=True)
    return False


# --------------------------------------------------------------------------- #
# Filter relaxation (ported from _zero_result_guidance, jrag.py:4187)         #
# --------------------------------------------------------------------------- #


def _filter_relaxation(
    filt: dict | None,
    filter_kind: str | None,
    scope: dict[str, str],
    graph: Any,
    identifier: str | None,
) -> FilterRelaxation:
    """For each constrained scope dim, tally where identifier/all matches live.

    Ports ``_zero_result_guidance``'s tally-and-suggest-most-common logic into the
    structured ``FilterRelaxation`` payload, parameterized on a filter-dims dict
    + graph (NOT ``argparse.Namespace``).
    """
    constrained: dict[str, str] = {}
    for source in (filt or {}, scope or {}):
        for dim in _RELAXABLE_DIMS:
            val = source.get(dim) if isinstance(source, dict) else None
            if isinstance(val, str) and val.strip() and dim not in constrained:
                constrained[dim] = val.strip()

    per_dimension: list[FilterRelaxationDim] = []
    for dim, val in constrained.items():
        try:
            total, suggested = _tally_dim(graph, dim, identifier)
        except Exception:  # noqa: BLE001 - relaxation is best-effort
            log.debug("filter relaxation tally failed for dim=%s", dim, exc_info=True)
            total, suggested = 0, None
        per_dimension.append(
            FilterRelaxationDim(
                dimension=dim,
                constrained_value=val,
                matches_under_relaxation=total,
                suggested_value=suggested,
            )
        )
    return FilterRelaxation(per_dimension=per_dimension)


def _tally_dim(
    graph: Any, dim: str, identifier: str | None,
) -> tuple[int, str | None]:
    """Count symbols (optionally matching ``identifier``) grouped by ``dim``.

    Returns (total_matches, most_common_bucket). Mirrors the probe+tally+top-3
    shape of ``_zero_result_guidance`` but reads the graph directly (no mcp_v2
    import) and returns structured values instead of a human string.
    """
    params: dict[str, Any] = {}
    where = ["s.module IS NOT NULL"] if dim == "module" else []
    # module_counts/microservice_counts count resolved type-symbols; mirror that
    # by restricting to resolved symbols for scope dims so suggestions are stable.
    if dim in ("module", "microservice", "role"):
        where.append("s.resolved = true")
    if identifier:
        where.append(
            "(toLower(s.name) CONTAINS toLower($needle) "
            "OR toLower(s.fqn) CONTAINS toLower($needle))"
        )
        params["needle"] = identifier
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    query = (
        f"MATCH (s:Symbol) {where_clause} "
        f"RETURN s.{dim} AS bucket, count(*) AS n ORDER BY n DESC LIMIT 10"
    )
    rows = graph._rows(query, params)  # noqa: SLF001
    buckets = [
        (str(r.get("bucket") or ""), int(r.get("n") or 0))
        for r in rows
        if r.get("bucket")
    ]
    total = sum(n for _, n in buckets)
    suggested = buckets[0][0] if buckets else None
    return total, suggested


# --------------------------------------------------------------------------- #
# Vocabulary context (nl_miss)                                                #
# --------------------------------------------------------------------------- #


def _build_vocabulary_context(graph: Any, vocab: VocabularyIndex) -> VocabularyContext:
    """Assemble project vocabulary stats to inform query refinement."""
    top_modules = sorted(graph.module_counts().items(), key=lambda kv: -kv[1])[:5]
    top_microservices = sorted(graph.microservice_counts().items(), key=lambda kv: -kv[1])[:5]

    role_counts: Counter = Counter()
    token_counts: Counter = Counter()
    for rec in vocab.records:
        if rec.role:
            role_counts[rec.role] += 1
        for tok in _camel_tokens(rec.simple_name):
            token_counts[tok] += 1

    roles = sorted(role_counts.items(), key=lambda kv: -kv[1])[:5]
    tokens = [tok for tok, _ in token_counts.most_common(10)]
    return VocabularyContext(
        top_modules=[(k, int(v)) for k, v in top_modules],
        top_microservices=[(k, int(v)) for k, v in top_microservices],
        roles_present=[(k, int(v)) for k, v in roles],
        frequent_name_tokens=tokens,
    )


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #


def _similarity(a: str, b: str) -> float:
    """difflib SequenceMatcher ratio on normalized names (∈ [0,1])."""
    return SequenceMatcher(None, a, b).ratio()


def _is_identifier_shaped(query: str) -> bool:
    """Predicate: does ``query`` look like an identifier (not natural language)?

    Identifier-shaped = a CamelCase token, dotted FQN, or ``Cls#member`` with no
    spaces, no NL punctuation, at least one alphanumeric, and not a lone stopword.
    Extends the spirit of ``_find_has_identifier_shaped_filter`` to free text.
    """
    q = query.strip()
    if not q or " " in q:
        return False
    if any(ch in q for ch in "?,;:!'\""):
        return False
    if not any(ch.isalnum() for ch in q):
        return False
    if q.lower() in _STOPWORDS:
        return False
    return True


def _looks_like_node_id(s: str) -> bool:
    """True if ``s`` carries a Ladybug node-id prefix (sym:/route:/ucs:/...)."""
    return any(s.startswith(pfx) for pfx in _NODE_ID_PREFIXES)


def _extract_identifier(filt: dict | None, filter_kind: str | None) -> str | None:
    """Pull the identifier filter value (fqn_contains/path_contains/...) out of filt."""
    if not filt or not filter_kind:
        return None
    for fname in _IDENTIFIER_FILTER_FIELDS.get(filter_kind, ()):
        val = filt.get(fname)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _exact_symbol_exists(query: str, vocab: VocabularyIndex) -> bool:
    """True if ``query`` exactly resolves to a real (resolved) project symbol.

    Conservative false-absent guard: compares the query verbatim AND normalized
    against every record's simple_name and fqn. Only invoked on the
    ``not_in_project`` path (rare), so the O(n) scan is acceptable; it makes
    declaring a real symbol absent impossible. Resolved-only because a phantom
    match is handled as ``external`` upstream.
    """
    q = query.strip()
    if not q:
        return False
    q_norm = _normalize_name(q)
    for rec in vocab.records:
        if not rec.resolved:
            continue
        if rec.simple_name == q or rec.fqn == q:
            return True
        if _normalize_name(rec.simple_name) == q_norm or _normalize_name(rec.fqn) == q_norm:
            return True
    return False


def _build_node_ref(rec: SymbolRecord) -> NodeRef:
    """Build a NodeRef from a SymbolRecord (per the brief's field mapping)."""
    return NodeRef(
        id=rec.node_id,
        kind="symbol",
        fqn=rec.fqn,
        name=rec.simple_name,
        symbol_kind=rec.kind or None,
        module=rec.module,
        microservice=rec.microservice,
        role=rec.role,
    )


def _camel_tokens(name: str) -> list[str]:
    """Split a CamelCase identifier into tokens for vocabulary statistics."""
    if not name:
        return []
    tokens: list[str] = []
    cur = ""
    prev_lower = False
    for ch in name:
        if ch.isupper():
            if prev_lower and cur:
                tokens.append(cur.lower())
                cur = ch
            else:
                cur += ch
            prev_lower = False
        elif ch.isalnum():
            cur += ch
            prev_lower = ch.islower()
        else:
            if cur:
                tokens.append(cur.lower())
                cur = ""
            prev_lower = False
    if cur:
        tokens.append(cur.lower())
    return [t for t in tokens if len(t) > 1]


def _fallback_refine() -> AbsenceDiagnosis | None:
    """Minimal refine_query when diagnosis cannot complete (never raises)."""
    try:
        return AbsenceDiagnosis(
            verdict="refine_query",
            cause="identifier_miss",
            message="Unable to diagnose the empty result; refine the query and retry.",
        )
    except Exception:  # noqa: BLE001
        return None
