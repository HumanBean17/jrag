#!/usr/bin/env python3
"""Dependency-free scoring & dedup primitives shared by the search backends.

Imported by both the vector backend (`search_lancedb`) and the lexical backend
(`search_lexical`). This module MUST NOT import lancedb / torch /
sentence_transformers / cocoindex — it is imported on graph-only (macOS Intel)
installs where those packages are absent (see pyproject.toml PEP 508 markers).

Everything here is pure-Python dict/list math with no third-party deps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Name of the LadybugDB FTS (Okapi BM25) index over Symbol.search_text (fork A).
# Shared by the build path (build_ast_graph._ensure_symbol_fts_index) and the
# query path (search_lexical.run_lexical_search) so the two never drift.
SYMBOL_FTS_INDEX = "sym_fts"

# Over-fetch multiplier for dedup: fetch 4x to absorb per-FQN chunk multiplicity
# so that after collapsing by primary_type_fqn, a page stays full and the +1
# truncation sentinel survives. The formula: need = max((limit + offset) * 4, limit + offset + 1)
DEDUP_OVERFETCH = 4

_IMPORT_DISTANCE_PENALTY = 0.08
_IMPORT_HYBRID_SCORE_FACTOR = 0.88

# Bonus for chunks whose declared symbols (method / field names) share tokens with
# the query. Behavioural queries like "what happens when a client message arrives"
# should float chunks containing `processClientMessage` above ones that only
# enqueue; this is a cheap, query-dependent signal computed at rank time.
_SYMBOL_MATCH_BONUS_PER_HIT = 0.03
_SYMBOL_MATCH_BONUS_CAP = 0.06

# Action verbs that typically mark behavioural entry points in this codebase.
# A chunk whose symbols begin with one of these verbs earns a small flat bump
# — again only for java chunks and only when role-filtering is off.
_ACTION_VERB_PREFIXES: tuple[str, ...] = (
    "process", "handle", "on", "pick", "select", "assign",
    "notify", "dispatch", "publish", "consume", "route",
    "trigger", "enqueue", "distribute", "update", "create",
    "apply", "resolve", "reassign", "close", "open",
)
_ACTION_VERB_BONUS = 0.02

# Type-name overlap bonus. The class name is a much stronger discovery signal
# than any individual method, because class naming in this codebase encodes
# the domain concept (`DistributionChunkService`, `OperatorSessionService`,
# `JoinOperatorController`). So we reward overlap between query tokens and the
# simple name of `primary_type_fqn` more heavily than per-method overlap, and
# we stack it on top of the existing `_symbol_bonus`.
_TYPE_MATCH_BONUS_PER_HIT = 0.05
_TYPE_MATCH_BONUS_CAP = 0.10

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "as", "or",
    "and", "but", "if", "then", "else", "when", "what", "how", "why", "does",
    "do", "did", "has", "have", "had", "this", "that", "these", "those", "it",
    "its", "new", "no", "not", "will", "would", "should", "can", "could",
    "may", "might", "happens", "happen", "happened", "get", "gets", "got",
})

# Role-aware reweighting for Java chunks. Positive values favour actionable
# behavioural code (entrypoints, orchestrators, integrations) over configuration,
# schema, and persistence stubs for "what happens when..."-style queries.
# Applied to the similarity score (higher = better); distance-based sort subtracts
# the weight. Skipped when caller filters explicitly by role.
_ROLE_SCORE_WEIGHTS: dict[str, float] = {
    "CONTROLLER": 0.10,
    "SERVICE": 0.08,
    "CLIENT": 0.06,
    "COMPONENT": 0.03,
    "REPOSITORY": 0.02,
    "MAPPER": 0.00,
    "OTHER": 0.00,
    "ENTITY": -0.06,
    "CONFIG": -0.10,
    # DTOs are passive data carriers; they almost never answer "how/what
    # happens" queries. Penalty is slightly stronger than ENTITY so a DTO
    # with a great embedding match still loses to a mediocre SERVICE hit.
    "DTO": -0.08,
}


def _rrf_max(num_lists: int, k: int = 60) -> float:
    """Return the theoretical maximum RRF score for N-list fusion.

    Reciprocal Rank Fusion (RRF) bounds each contribution to ≤ 1/(rank + k).
    For N fused lists, the maximum possible sum is N/(k + 1) (achieved when
    an item ranks #1 across all lists).

    Args:
        num_lists: Number of ranked lists being fused (e.g., 2 for vector+lexical).
        k: The RRF constant (default 60 per the original paper).

    Returns:
        The maximum RRF contribution: num_lists / (k + 1).
    """
    return num_lists / (k + 1)


# Allowed list names in a RankConfig: vector is always required (it is the
# backbone retrieval signal); graph and bm25 are optional fusion participants.
# NOTE: "bm25" is honored as a config element from Task 2, but the BM25 candidate
# fetch is wired only in Task 4 — until then a configured "bm25" list yields no
# candidates and behaves as a 2-list (vector+graph) fusion.
_RANK_LIST_NAMES: frozenset[str] = frozenset({"vector", "graph", "bm25"})


@dataclass(frozen=True)
class RankConfig:
    """Which ranked lists to fuse and the RRF constant to fuse them with.

    This is a dep-free value object (no lancedb/torch) so it can be constructed
    on every install flavor, including graph-only (macOS Intel). It is plumbed
    through ``run_search`` → ``_graph_expand_merge`` to control (a) which lists
    contribute to the final RRF fusion and (b) the ``k`` constant passed into
    ``_rrf_merge``.

    Attributes:
        lists: Subset of ``{"vector", "graph", "bm25"}``. Must contain
            ``"vector"`` (the backbone retrieval signal) and be non-empty.
        rrf_k: The RRF constant (default 60 per the original paper). Must be ≥ 1.
    """

    lists: frozenset[str]
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if not isinstance(self.lists, frozenset) or not self.lists:
            raise ValueError("RankConfig.lists must be a non-empty frozenset")
        if "vector" not in self.lists:
            raise ValueError(
                "RankConfig.lists must contain 'vector' (the backbone signal)"
            )
        unknown = self.lists - _RANK_LIST_NAMES
        if unknown:
            raise ValueError(
                f"RankConfig.lists has unknown names {sorted(unknown)!r}; "
                f"allowed: {sorted(_RANK_LIST_NAMES)!r}"
            )
        if not isinstance(self.rrf_k, int) or self.rrf_k < 1:
            raise ValueError(f"RankConfig.rrf_k must be an int >= 1, got {self.rrf_k!r}")


# Production default: ship the 3-list config (vector+graph+bm25). The "bm25"
# element is inert until Task 4 wires the BM25 candidate fetch; until then this
# is effectively a 2-list (vector+graph) fusion, identical to pre-Task-2 behavior.
DEFAULT_RANK_CONFIG = RankConfig(lists=frozenset({"vector", "graph", "bm25"}), rrf_k=60)

# Eval convenience: the historical 2-list (vector+graph) fusion, used by
# evaluation harnesses that do not want the bm25 list even after Task 4.
BASELINE_2LIST_CONFIG = RankConfig(lists=frozenset({"vector", "graph"}), rrf_k=60)


# Theoretical maximum for hybrid composite score (used for display normalization).
# Hybrid sort metric: raw_rrf * (import_factor if import_heavy else 1)
#                   + role_weight + symbol_bonus
# where raw_rrf ≤ 2/(k+1) for 2-list RRF, role_weight ≤ max(_ROLE_SCORE_WEIGHTS),
# and symbol_bonus ≤ _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS.
# The import factor is ≤ 1, so we use the raw max (derived via _rrf_max(2)).
_HYBRID_SCORE_MAX = _rrf_max(2) + max(_ROLE_SCORE_WEIGHTS.values()) + _SYMBOL_MATCH_BONUS_CAP + _TYPE_MATCH_BONUS_CAP + _ACTION_VERB_BONUS


def _query_tokens(query: str) -> set[str]:
    """Lowercased alpha-only tokens from the query, minus stopwords, len >= 3.

    Used to score symbol-name overlap; we keep it simple and locale-free.
    """
    out: set[str] = set()
    cur: list[str] = []

    def _flush() -> None:
        if cur:
            tok = "".join(cur).lower()
            cur.clear()
            if len(tok) >= 3 and tok not in _STOPWORDS:
                out.add(tok)

    for c in query:
        if c.isalpha():
            cur.append(c)
        else:
            _flush()
    _flush()
    return out


def _split_identifier(name: str) -> list[str]:
    """camelCase / snake_case -> lowercase token list."""
    parts: list[str] = []
    cur: list[str] = []
    for c in name:
        if c == "_":
            if cur:
                parts.append("".join(cur).lower())
                cur = []
        elif c.isupper() and cur:
            parts.append("".join(cur).lower())
            cur = [c]
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur).lower())
    return [p for p in parts if p]


# Alphanumeric-word extractor for FTS query building (mirrors the index side, which
# runs the same regex over name/fqn/signature/annotations/capabilities/package fields).
_FTS_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def build_fts_query(text: str) -> str:
    """Tokenize a search query into the ``Symbol.search_text`` token space (fork A).

    ``search_text`` is indexed from ``_split_identifier`` tokens (camelCase / snake_case
    split, lowercased). LadybugDB FTS's own tokenizer does NOT split camelCase, so a raw
    pasted identifier like ``DistributionChunkService`` would match nothing — this
    extracts alphanumeric words from the query and splits each via ``_split_identifier``
    so the query lands in the index's token space. Tokens shorter than 2 chars are
    dropped (mirrors the index side); duplicates collapse. Returns ``""`` for a query
    with no usable tokens — the caller then falls back to the heuristic / role listing.
    """
    out: list[str] = []
    seen: set[str] = set()
    for word in _FTS_WORD_RE.findall(text or ""):
        for tok in _split_identifier(word):
            if len(tok) >= 2 and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return " ".join(out)


def _symbol_bonus(r: dict, query_toks: set[str]) -> float:
    """Symbol-name overlap + action-verb bump for java chunks.

    Caps at `_SYMBOL_MATCH_BONUS_CAP + _ACTION_VERB_BONUS` to avoid runaway
    ranks on chunks declaring many symbols.
    """
    if str(r.get("_kind", "")) != "java":
        return 0.0
    raw = r.get("symbols") or []
    if isinstance(raw, str):
        # Legacy JSON-encoded list column; parse defensively.
        try:
            parsed = json.loads(raw)
            raw = parsed if isinstance(parsed, list) else []
        except Exception:
            raw = []
    symbols = [str(s) for s in raw if s]

    overlap_hits = 0
    has_action = False
    for s in symbols:
        bare = s.split("(", 1)[0].strip()
        if not bare:
            continue
        toks = _split_identifier(bare)
        if toks:
            if toks[0] in _ACTION_VERB_PREFIXES:
                has_action = True
            if query_toks & set(toks):
                overlap_hits += 1

    bonus = min(overlap_hits * _SYMBOL_MATCH_BONUS_PER_HIT, _SYMBOL_MATCH_BONUS_CAP)
    if has_action:
        bonus += _ACTION_VERB_BONUS

    # Type-name overlap: strongest single lexical signal for "which class is
    # the answer?" queries. Uses the simple name of primary_type_fqn.
    fqn = str(r.get("primary_type_fqn") or "")
    if fqn:
        simple = fqn.rsplit(".", 1)[-1]
        type_toks = set(_split_identifier(simple))
        type_hits = len(query_toks & type_toks)
        if type_hits:
            bonus += min(type_hits * _TYPE_MATCH_BONUS_PER_HIT, _TYPE_MATCH_BONUS_CAP)
    return bonus


def _role_weight(r: dict) -> float:
    """Effective role weight for a row, captured into `_score_components.role_weight`."""
    comps = r.setdefault("_score_components", {})
    cached = comps.get("role_weight")
    if cached is not None:
        return float(cached)
    if r.get("_skip_role_weight") or str(r.get("_kind", "")) != "java":
        comps["role_weight"] = 0.0
        return 0.0
    role = (r.get("role") or "").upper()
    w = _ROLE_SCORE_WEIGHTS.get(role, 0.0)
    comps["role_weight"] = w
    return w


def _apply_symbol_bonus(rows: list[dict], query_toks: set[str]) -> None:
    """Pre-compute symbol-match bonus into `_score_components.symbol_bonus`."""
    if not query_toks:
        return
    for r in rows:
        if r.get("_skip_role_weight"):
            # When the caller locked role, respect their intent everywhere.
            continue
        b = _symbol_bonus(r, query_toks)
        if b:
            r.setdefault("_score_components", {})["symbol_bonus"] = b


def l2_distance_to_score(distance: float) -> float:
    """Map L2 distance to a similarity score for unit-normalized embeddings."""
    return 1.0 - distance * distance / 2.0


# Display-score denominator for the vector backend. Unit-normalized embeddings
# have L2 distance in [0, 2]; the cosine map ``l2_distance_to_score`` (1 - d²/2)
# goes NEGATIVE past √2 ≈ 1.414 and clamps to 0. Weak-but-best semantic matches
# (e.g. a lone keyword like "controller") commonly sit at d ≈ 1.5, so EVERY hit
# clamps to score=0.000 even though the ranking is correct. ``vector_display_score``
# instead normalizes the effective (bonus-adjusted) distance over the full
# unit-embedding range, so a top-ranked hit stays visibly non-zero. Role/symbol
# bonuses reduce the effective distance and so raise the displayed score,
# keeping it rank-monotonic with the distance-based sort key.
_VECTOR_DISTANCE_REF = 2.0


def vector_display_score(effective_distance: float) -> float:
    """Displayed vector score in [0, 1] from the effective (bonus-adjusted) distance.

    Bounded linear normalization over the unit-embedding L2 range [0, 2]: lower
    distance → higher score. Unlike ``l2_distance_to_score`` (which goes
    negative past √2 and clamps a correctly-ranked top hit to 0.000), this keeps
    a top result visibly non-zero while staying rank-monotonic with the sort key.
    """
    return _clamp01(1.0 - effective_distance / _VECTOR_DISTANCE_REF)


def _effective_distance(comps: dict[str, float]) -> float:
    """Compute the adjusted distance used for sorting.

    Matches _vector_sort_key logic: distance + import_penalty - role_weight - symbol_bonus.
    """
    d = comps.get("distance", 0.0)
    d += comps.get("import_penalty", 0.0)
    d -= comps.get("role_weight", 0.0)
    d -= comps.get("symbol_bonus", 0.0)
    return d


def _clamp01(x: float) -> float:
    """Clamp a value to the [0.0, 1.0] range."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


# Matches a Java top-level type declaration and captures its simple name. Mirrors
# the heuristic in ``ast.chunk_heuristics._JAVA_TYPE`` but is duplicated here so
# this module stays dependency-free (importable on graph-only Intel installs).
_JAVA_TYPE_DECL_RE = re.compile(
    r"\b(?:public\s+|private\s+|protected\s+|sealed\s+|non-sealed\s+|final\s+|"
    r"abstract\s+|static\s+)*"
    r"(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def declaration_line_number(
    text: str | None, anchor_line: int | None, type_name: str | None = None
) -> int | None:
    """Absolute 1-based line of the Java type declaration within chunk ``text``.

    LanceDB chunks are anchored at the chunk's first source line, which for a
    file-spanning chunk is the package/import line (``anchor_line`` = 1) while
    the ``class``/``interface`` declaration sits several lines down. Without
    this, hits render as ``File.java:1`` even though the symbol is declared
    later (F8). Returns ``anchor_line + i`` for the first matching declaration
    (pinned to ``type_name`` when given, so a nested type doesn't win), or
    ``anchor_line`` unchanged when no declaration is found in the chunk.

    Comment-aware: Javadoc/line/block-comment lines that merely MENTION the type
    name (e.g. ``* This class Bar handles...``) are skipped so the returned line
    is the real declaration, not a comment above it.
    """
    if not text or anchor_line is None:
        return anchor_line
    in_block = False
    for i, raw in enumerate(text.splitlines()):
        # Drop a trailing ``// ...`` line comment before any matching (a ``//``
        # inside a string literal is unrealistic for a declaration line).
        code = raw.split("//", 1)[0]
        stripped = code.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            # Single-line ``/* ... */`` -> skip without entering block state.
            if "*/" not in stripped[2:]:
                in_block = True
            continue
        if not stripped or stripped.startswith("*"):
            # Blank or a Javadoc continuation line (`` * ...``).
            continue
        m = _JAVA_TYPE_DECL_RE.search(code)
        if m and (not type_name or m.group(1) == type_name):
            return anchor_line + i
    return anchor_line


def explain_score_components(
    comps: dict[str, float] | None,
    *,
    role: str | None = None,
    hybrid: bool = False,
    graph_expanded: bool = False,
    lexical: bool = False,
) -> str:
    """Compact human-readable 'why' string for a ranked hit.

    Joins the interesting components of `_score_components` in a stable order
    so agents can reason about rankings without chasing raw floats. Returns
    "" if there's nothing worth mentioning.
    """
    if not comps:
        comps = {}
    parts: list[str] = []
    if lexical:
        rel = comps.get("lexical_relevance")
        if rel is not None:
            parts.append(f"relevance={float(rel):.2f}")
        nm = comps.get("name_match")
        if nm is not None:
            parts.append(f"name={float(nm):.2f}")
        ty = comps.get("type_match")
        if ty:
            parts.append(f"type:{float(ty):+.2f}")
        fq = comps.get("fqn_match")
        if fq:
            parts.append(f"fqn:{float(fq):+.2f}")
    elif hybrid:
        # Prefer rrf_raw (added by PR-SEARCH-1a) for explanation
        rrf = comps.get("rrf_raw") or comps.get("hybrid_rrf")
        if rrf is not None:
            parts.append(f"rrf={float(rrf):.3f}")
    else:
        d = comps.get("distance")
        if d is not None:
            parts.append(f"dist={float(d):.2f}")
    rw = comps.get("role_weight")
    if rw:
        label = f"role:{role}" if role else "role"
        parts.append(f"{label}:{float(rw):+.02f}")
    sb = comps.get("symbol_bonus")
    if sb:
        parts.append(f"symbol:{float(sb):+.02f}")
    ip = comps.get("import_penalty")
    if ip:
        parts.append(f"import_penalty:{float(ip):+.02f}")
    if graph_expanded:
        parts.append("graph")
    return " ".join(parts)


def _dedup_by_fqn(rows: list[dict], dedup_by_fqn: bool = True) -> list[dict]:
    """Deduplicate rows by primary_type_fqn (java table only).

    When dedup_by_fqn is True, collapses multiple chunks of the same
    primary_type_fqn into one row (first-seen-wins, since rows are pre-sorted
    so the first is the best chunk). Each survivor gets a _chunks_collapsed
    field (>=1) counting how many rows were collapsed into it.

    Rows without primary_type_fqn (sql/yaml tables) get a unique __id:<id>
    key so they pass through unchanged (each row is unique).

    When dedup_by_fqn is False, returns rows unchanged (regression guard).
    """
    if not dedup_by_fqn:
        # Non-dedup path: return unchanged, byte-identical to prior behavior
        return rows

    deduped: list[dict] = []
    seen_keys: dict[str, dict] = {}
    collapsed_counts: dict[str, int] = {}

    for row in rows:
        # Build dedup key: primary_type_fqn for java rows, unique __id:<id> for sql/yaml
        fqn = row.get("primary_type_fqn")
        if fqn:
            key = str(fqn)
        else:
            # sql/yaml rows have no primary_type_fqn → unique key per row
            row_id = row.get("id") or id(row)
            key = f"__id:{row_id}"

        if key not in seen_keys:
            # First occurrence: keep it
            seen_keys[key] = row
            collapsed_counts[key] = 1
            deduped.append(row)
        else:
            # Duplicate: increment collapse count, discard this row
            collapsed_counts[key] += 1

    # Annotate each survivor with _chunks_collapsed
    for row in deduped:
        fqn = row.get("primary_type_fqn")
        if fqn:
            key = str(fqn)
        else:
            row_id = row.get("id") or id(row)
            key = f"__id:{row_id}"
        row["_chunks_collapsed"] = collapsed_counts[key]

    return deduped
