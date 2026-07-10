"""Vocabulary index for absence diagnosis (PR-ABS-1).

A VocabularyIndex builds a search-optimized projection from a LadybugGraph's
Symbol nodes, persisting as a versioned JSON sidecar. It provides bounded-time
lookup for did-you-mean candidates and external membership checks.

Consumed by PR-ABS-2 (diagnosis ranking) and PR-ABS-3 (MCP tools).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "SymbolRecord",
    "VocabularyIndex",
    "VocabIndexStale",
    "get_vocabulary_index",
    "reset_cache",
    "VOCAB_INDEX_FILENAME",
]

VOCAB_INDEX_FILENAME = "vocab_index.json"

# Sidecar schema version. Bump when the on-disk JSON shape changes; load() rejects
# a mismatch as stale (→ rebuild) so an old-format sidecar is never misread.
FORMAT_VERSION = 1


class VocabIndexStale(Exception):
    """Raised when loading a vocab index with stale ontology_version."""

    pass


@dataclass
class SymbolRecord:
    """A single symbol record from the graph.

    Attributes:
        node_id: Ladybug node ID
        fqn: Fully qualified name
        simple_name: Simple name (last segment)
        normalized_name: Lowercased simple name with signatures stripped
        kind: Symbol kind (class, method, field, etc.)
        module: Maven module (if available)
        microservice: Microservice label (if available)
        role: Symbol role (Controller, Service, Repository, etc.)
        resolved: Whether the symbol resolved to a source location
    """
    node_id: str
    fqn: str
    simple_name: str
    normalized_name: str
    kind: str
    module: str | None
    microservice: str | None
    role: str | None
    resolved: bool


class VocabularyIndex:
    """Search-optimized vocabulary index built from LadybugGraph Symbol nodes.

    The index stores a flat list of SymbolRecords and an n-gram inverted index
    mapping q-grams to record indexes. This allows bounded-time lookup for
    did-you-mean candidates without scanning the entire vocabulary.

    Built at the end of graph build; persisted as a sidecar JSON; lazily rebuilt
    if missing or stale (ontology_version mismatch).
    """

    def __init__(
        self,
        records: list[SymbolRecord],
        ngram_index: dict[str, list[int]],
        q: int,
        _name_index: dict[str, list[int]] | None = None,
    ) -> None:
        self.records = records
        self.ngram_index = ngram_index
        self.q = q
        # Build name index for O(1) exact lookups (key: normalized_name -> record indices)
        if _name_index is None:
            self._name_index: dict[str, list[int]] = {}
            for idx, record in enumerate(records):
                norm = record.normalized_name
                if norm not in self._name_index:
                    self._name_index[norm] = []
                self._name_index[norm].append(idx)
        else:
            self._name_index = _name_index

    @property
    def symbol_count(self) -> int:
        return len(self.records)

    @classmethod
    def build(cls, graph: Any, *, q: int) -> "VocabularyIndex":
        """Build a vocabulary index from a LadybugGraph.

        Enumerates all Symbol nodes, builds SymbolRecords with normalized names,
        and constructs a q-gram inverted index for candidate lookup.

        Args:
            graph: LadybugGraph instance
            q: N-gram length (typically 3)

        Returns:
            VocabularyIndex ready for queries
        """
        # Query all Symbol nodes with proper column aliases
        query = """
            MATCH (s:Symbol)
            RETURN s.id AS id, s.kind AS kind, s.name AS name, s.fqn AS fqn,
                   s.package AS package, s.module AS module, s.microservice AS microservice,
                   s.filename AS filename, s.start_line AS start_line, s.end_line AS end_line,
                   s.start_byte AS start_byte, s.end_byte AS end_byte, s.modifiers AS modifiers,
                   s.annotations AS annotations, s.capabilities AS capabilities, s.role AS role,
                   s.signature AS signature, s.parent_id AS parent_id, s.resolved AS resolved
        """
        rows = graph._rows(query, {})

        records: list[SymbolRecord] = []
        for row in rows:
            record = _row_to_symbol_record(row)
            records.append(record)

        # Build n-gram index from normalized names
        ngram_index: dict[str, list[int]] = {}
        for idx, record in enumerate(records):
            grams = _qgrams(record.normalized_name, q)
            for gram in grams:
                if gram not in ngram_index:
                    ngram_index[gram] = []
                ngram_index[gram].append(idx)

        return cls(records=records, ngram_index=ngram_index, q=q)

    def save(self, path: Path, *, ontology_version: int) -> None:
        """Save the vocabulary index to a JSON sidecar.

        Args:
            path: Destination path for the sidecar
            ontology_version: Current graph ontology version (for staleness detection)
        """
        import time

        data = {
            "format_version": FORMAT_VERSION,
            "ontology_version": ontology_version,
            "built_at": int(time.time()),
            "symbol_count": self.symbol_count,
            "q": self.q,
            "records": [
                {
                    "node_id": r.node_id,
                    "fqn": r.fqn,
                    "simple_name": r.simple_name,
                    "normalized_name": r.normalized_name,
                    "kind": r.kind,
                    "module": r.module,
                    "microservice": r.microservice,
                    "role": r.role,
                    "resolved": r.resolved,
                }
                for r in self.records
            ],
            "ngrams": self.ngram_index,
            # _name_index is intentionally NOT persisted: it is derivable from
            # records and rebuilt in __init__ on load (single source of truth,
            # no sidecar bloat).
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write — dump to a temp sibling, then os.replace onto the target.
        # A crash mid-write leaves either the previous complete file or the new
        # complete file, never a truncated/corrupt sidecar (readers see one or
        # the other atomically; os.replace is atomic on the same filesystem).
        tmp_path = path.with_name(path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, path)

        log.debug(f"VocabularyIndex saved to {path} ({self.symbol_count} symbols)")

    @classmethod
    def load(cls, path: Path) -> "VocabularyIndex":
        """Load a vocabulary index from a JSON sidecar.

        Args:
            path: Path to the sidecar file

        Returns:
            VocabularyIndex

        Raises:
            VocabIndexStale: If sidecar format_version or ontology_version
                doesn't match expected
        """
        from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        # Check format version (sidecar JSON schema) first.
        if data.get("format_version") != FORMAT_VERSION:
            raise VocabIndexStale(
                f"Vocab index format_version {data.get('format_version')} "
                f"does not match expected {FORMAT_VERSION}"
            )

        # Check ontology version (graph schema the index was built against).
        if data.get("ontology_version") != ONTOLOGY_VERSION:
            raise VocabIndexStale(
                f"Vocab index ontology version {data.get('ontology_version')} "
                f"does not match expected {ONTOLOGY_VERSION}"
            )

        records = [
            SymbolRecord(
                node_id=r["node_id"],
                fqn=r["fqn"],
                simple_name=r["simple_name"],
                normalized_name=r["normalized_name"],
                kind=r["kind"],
                module=r.get("module"),
                microservice=r.get("microservice"),
                role=r.get("role"),
                resolved=r["resolved"],
            )
            for r in data["records"]
        ]

        # _name_index is rebuilt from records in __init__ (not persisted).
        return cls(
            records=records,
            ngram_index=data["ngrams"],
            q=data["q"],
        )

    def lookup(self, name: str, *, limit: int) -> list[SymbolRecord]:
        """Lookup candidate records by name using n-gram overlap.

        This returns candidate records ONLY; ranking by similarity is done
        in PR-ABS-2 (absence_diagnosis module) to avoid circular imports.

        Args:
            name: Query name (can be typoed)
            limit: Maximum number of candidates to return

        Returns:
            List of candidate SymbolRecord (up to limit), ordered by n-gram overlap count
        """
        # First, check for exact match on simple_name using O(1) dict lookup (fast path)
        # Return ALL matching records since overloaded names matter
        normalized = _normalize_name(name)
        if normalized in self._name_index:
            exact_matches = [self.records[idx] for idx in self._name_index[normalized]]
            # Filter to only those where simple_name matches exactly (case-sensitive)
            exact_simple_matches = [r for r in exact_matches if r.simple_name == name]
            if exact_simple_matches:
                log.debug(f"lookup({name}): exact match found")
                return exact_simple_matches

        # No exact match, use n-gram overlap
        # Extract q-grams from query
        grams = _qgrams(normalized, self.q)

        # Count n-gram matches per record index
        match_counts: dict[int, int] = {}
        for gram in grams:
            if gram in self.ngram_index:
                for idx in self.ngram_index[gram]:
                    match_counts[idx] = match_counts.get(idx, 0) + 1

        # Sort by match count (descending) to get candidates with most overlap first
        sorted_idxs = sorted(match_counts.keys(), key=lambda idx: match_counts[idx], reverse=True)

        # Debug logging
        log.debug(f"lookup({name}): normalized={normalized}, grams={grams[:5]}, candidates={len(sorted_idxs)}")

        # Return top candidates
        candidates = [self.records[idx] for idx in sorted_idxs[:limit]]
        return candidates

    def is_external(self, name: str) -> tuple[bool, str | None]:
        """Check if a name refers to an external symbol.

        Returns (is_external, reason) where reason is one of:
        - "prefix": FQN matches an external library prefix (java.*, javax.*, etc.)
        - "phantom": Symbol exists in graph but is unresolved (phantom)
        - None: Symbol is a real project symbol

        Args:
            name: Simple name or FQN to check

        Returns:
            (is_external, reason) tuple
        """
        from java_codebase_rag.graph.ladybug_queries import _is_external_fqn, _EXTERNAL_PREFIXES

        # First, check if it's an external prefix (highest priority)
        if _is_external_fqn(name):
            return (True, "prefix")

        # Also check simple name against external prefixes
        for prefix in _EXTERNAL_PREFIXES:
            if name.startswith(prefix):
                return (True, "prefix")

        # Check if name matches any record in our vocabulary using O(1) dict lookup
        normalized = _normalize_name(name)
        matching_indices = self._name_index.get(normalized, [])
        matching_record = None
        for idx in matching_indices:
            rec = self.records[idx]
            if rec.simple_name == name or rec.fqn == name:
                matching_record = rec
                break

        if matching_record:
            # If the symbol is unresolved, it's a phantom
            if not matching_record.resolved:
                return (True, "phantom")
            # Otherwise it's a real project symbol
            return (False, None)

        # Not found and doesn't look external
        return (False, None)


# Module-level cache for get_vocabulary_index
_vocab_cache: dict[str, VocabularyIndex] = {}


def get_vocabulary_index(graph: Any, cfg: Any) -> VocabularyIndex:
    """Get or build a vocabulary index for the given graph.

    This is the primary entry point for the diagnosis layer (PR-ABS-2) and
    tools (PR-ABS-3). It implements lazy backfill: tries to load from sidecar,
    builds from graph on miss/stale, and caches the result.

    Args:
        graph: LadybugGraph instance
        cfg: ResolvedOperatorConfig instance

    Returns:
        VocabularyIndex (cached or newly built)
    """
    from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION

    # Determine graph db path for cache key
    db_path = graph.db_path if hasattr(graph, 'db_path') else str(cfg.ladybug_path)
    sidecar_path = Path(db_path).parent / VOCAB_INDEX_FILENAME

    # Check cache
    if db_path in _vocab_cache:
        return _vocab_cache[db_path]

    # Try loading from sidecar
    try:
        index = VocabularyIndex.load(sidecar_path)
        _vocab_cache[db_path] = index
        log.debug(f"Loaded vocabulary index from {sidecar_path}")
        return index
    except Exception as e:
        # Stale (VocabIndexStale), missing (FileNotFoundError), or corrupt
        # (JSONDecodeError/KeyError) — all subsumed by Exception; rebuild.
        log.debug(f"Vocab index missing/stale/corrupt ({e}), rebuilding from graph")

        # Build from graph. Coerce q to int (mirror absence_diagnosis.py's
        # int(getattr(cfg, ...)) pattern): a non-int cfg.absence_ngram_q (e.g. a
        # MagicMock cfg from a leaked test mock, or a YAML string) would otherwise
        # crash _qgrams at `len(text) < q` ('int < MagicMock'). Default 3 = the
        # config default for absence_ngram_q.
        q = int(getattr(cfg, "absence_ngram_q", 3) or 3)
        index = VocabularyIndex.build(graph, q=q)

        # Save to sidecar (best-effort)
        try:
            index.save(sidecar_path, ontology_version=ONTOLOGY_VERSION)
        except Exception as save_err:
            log.warning(f"Failed to save vocab index to {sidecar_path}: {save_err}")

        # Cache and return
        _vocab_cache[db_path] = index
        return index


def reset_cache() -> None:
    """Reset the module-level vocabulary index cache.

    Exposed for tests that need to simulate a fresh start or different graph paths.
    """
    global _vocab_cache
    _vocab_cache = {}


# ---- Helper functions ----


def _row_to_symbol_record(row: dict[str, Any]) -> SymbolRecord:
    """Convert a Ladybug row to a SymbolRecord."""
    from java_codebase_rag.graph.ladybug_queries import _type_part_fqn

    fqn = row.get("fqn") or ""
    name = row.get("name") or ""

    return SymbolRecord(
        node_id=row.get("id") or "",
        fqn=fqn,
        simple_name=name,
        normalized_name=_normalize_name(name),
        kind=row.get("kind") or "",
        module=row.get("module"),
        microservice=row.get("microservice"),
        role=row.get("role"),
        resolved=bool(row.get("resolved", True)),
    )


def _normalize_name(name: str) -> str:
    """Normalize a symbol name for n-gram indexing.

    Strips:
    - Generic signatures (e.g., List<String> → List)
    - Method signatures (e.g., method(Param) → method)
    - Parentheses and angle brackets

    Returns lowercase result.
    """
    # Remove generic signatures
    normalized = name.split("<")[0]
    # Remove method signatures
    normalized = normalized.split("(")[0]
    # Remove hash suffix (e.g., method#signature)
    normalized = normalized.split("#")[0]
    return normalized.lower()


def _qgrams(text: str, q: int) -> list[str]:
    """Extract q-grams from text.

    Args:
        text: Input string
        q: Gram length

    Returns:
        List of q-grams (substrings of length q)
    """
    if len(text) < q:
        return [text] if text else []
    return [text[i:i + q] for i in range(len(text) - q + 1)]
