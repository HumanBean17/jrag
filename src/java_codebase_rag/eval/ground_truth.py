"""Eval ground-truth — Tier-A auto generator + Tier-B file loader.

Tier-A derives labeled queries deterministically from indexed symbols (no
manual labeling). Tier-B loads hand-curated labeled queries from YAML/JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import yaml

from java_codebase_rag.search.search_scoring import _split_identifier


class SymbolLike(Protocol):
    """Structural type for symbols — duck-typed .fqn / .name."""

    fqn: str
    name: str


@dataclass(frozen=True)
class LabeledQuery:
    """A labeled retrieval query and its set of relevant Symbol FQNs."""

    query: str
    relevant: frozenset[str]
    tier: str


def build_tier_a(symbols: Iterable[SymbolLike]) -> list[LabeledQuery]:
    """Auto-generate labeled queries from each symbol's simple name.

    For each symbol two query strings are derived from its simple name:
      1. The original simple name verbatim (e.g. ``"DistributionChunkService"``)
         — matches identifier-joined index text.
      2. A space-joined lowercase token form (e.g. ``"distribution chunk service"``)
         produced via ``search_scoring._split_identifier`` so tokenization parity
         with the FTS index holds.

    Symbols whose simple name splits to fewer than 2 tokens or is shorter than
    3 characters are skipped (noise). Output is deterministic, sorted by
    ``(query, fqn)``; all entries carry ``tier="A"``.
    """
    out: list[LabeledQuery] = []
    for sym in symbols:
        name: str = sym.name
        if len(name) < 3:
            continue
        tokens = _split_identifier(name)
        if len(tokens) < 2:
            continue
        fqn: str = sym.fqn
        relevant = frozenset({fqn})
        # 1. identifier-joined form = ORIGINAL simple name (preserve case).
        out.append(LabeledQuery(name, relevant, "A"))
        # 2. space-joined lowercase token form.
        out.append(LabeledQuery(" ".join(tokens), relevant, "A"))
    out.sort(key=lambda q: (q.query, next(iter(q.relevant))))
    return out


def load_tier_b(path: str | Path) -> list[LabeledQuery]:
    """Load hand-curated Tier-B labeled queries from a YAML (``.yaml``/``.yml``)
    or JSON (``.json``) file.

    Schema: a list of ``{query: str, relevant: [str, ...]}`` objects.

    Raises:
        FileNotFoundError: if the path does not exist (the runner checks
            existence before calling, treating absence as "Tier-B disabled").
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Tier-B ground-truth file not found: {p}")

    suffix = p.suffix.lower()
    raw = p.read_text()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(raw)
    elif suffix == ".json":
        data = json.loads(raw)
    else:
        # Fall back to YAML (superset of JSON) for unknown extensions.
        data = yaml.safe_load(raw)

    out: list[LabeledQuery] = []
    for entry in data or []:
        out.append(
            LabeledQuery(
                query=str(entry["query"]),
                relevant=frozenset(entry.get("relevant", []) or []),
                tier="B",
            )
        )
    return out
