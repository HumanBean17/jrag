"""Question schema + loader/validator (Plan 1, Task 11).

Reads ``bench/questions/*.jsonl`` into ``Question`` records and enforces the
authoring protocol: closed category/difficulty/grading vocabularies, valid claim
refs, a corpus that exists in ``corpora.yml``, and — the anti-leakage guard — no
jrag/tool vocabulary inside the engineer-phrased question text.
"""
from __future__ import annotations

import glob as _glob
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATEGORIES = {
    "interface-impls", "upstream-consumers", "call-trace", "blast-radius",
    "cross-service", "role-listing", "semantic", "absence",
}
DIFFICULTIES = {"easy", "medium", "hard"}
CLAIMS = {"C1", "C2", "C3", "C4", "C5", "C6"}
GRADINGS = {
    "programmatic_set_match", "programmatic_jaccard", "programmatic_path_match",
    "programmatic_client_route_match", "llm_judge", "absence_check",
}
# Tool vocabulary that must NOT appear in engineer-phrased questions (whole-token).
# Using it would leak the answer method to the agent. Two tiers:
#  - LEAKAGE_JARGON: tokens with no natural-English use (tool/MCP/internal names).
#    Matched case-INSENSITIVELY — a shouty "MCP__JRAG" or "Ontology_Version" is
#    still a leak.
#  - LEAKAGE_EDGE_WORDS: graph-edge names that are ALSO ordinary English words.
#    Matched case-SENSITIVELY against the exact tool casing (uppercase edge
#    `:CALLS`, lowercase tool `neighbors`), so a natural "which method calls X"
#    does not false-positive. (Plain "neighbors" is rejected whenever it appears —
#    it is both the tool name and an English word; the frozen set simply avoids it.)
LEAKAGE_JARGON = {
    "HTTP_CALLS", "ASYNC_CALLS", "mcp__jrag", "ontology_version",
    "edge_types", "NodeFilter",
}
LEAKAGE_EDGE_WORDS = {
    "INJECTS", "IMPLEMENTS", "EXTENDS", "OVERRIDES", "DECLARES", "EXPOSES", "CALLS",
    "neighbors",
}
# Single back-compat surface (tests/importers read LEAKAGE_VOCAB).
LEAKAGE_VOCAB = LEAKAGE_JARGON | LEAKAGE_EDGE_WORDS
_LEAKAGE_JARGON_UPPER = {term.upper() for term in LEAKAGE_JARGON}

_NAME_RE = re.compile(r"^[a-z0-9-]+$")
_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


class ConfigError(ValueError):
    """Raised when a question record violates a schema/anti-leakage invariant."""


@dataclass(frozen=True)
class Question:
    id: str
    corpus: str
    category: str
    difficulty: str
    question: str
    oracle_source: str
    claim_refs: list[str]
    grading: str
    expected: dict | None = None
    oracle_params: dict = field(default_factory=dict)


def _corpora_names(corpora_path: str = "bench/corpora.yml") -> set[str]:
    raw = yaml.safe_load(Path(corpora_path).read_text(encoding="utf-8"))
    return {e["name"] for e in (raw or {}).get("corpora", [])}


def validate(q: Question, *, valid_corpora: set[str] | None = None) -> None:
    """Raise ``ConfigError`` on any schema/anti-leakage violation."""
    if not _NAME_RE.match(q.id):
        raise ConfigError(f"question id {q.id!r} must match ^[a-z0-9-]+$")
    if valid_corpora is None:
        valid_corpora = _corpora_names()
    if q.corpus not in valid_corpora:
        raise ConfigError(
            f"question {q.id!r}: corpus {q.corpus!r} not in corpora.yml ({sorted(valid_corpora)})"
        )
    if q.category not in CATEGORIES:
        raise ConfigError(f"question {q.id!r}: category {q.category!r} not in {sorted(CATEGORIES)}")
    if q.difficulty not in DIFFICULTIES:
        raise ConfigError(f"question {q.id!r}: difficulty {q.difficulty!r} not in {sorted(DIFFICULTIES)}")
    if q.grading not in GRADINGS:
        raise ConfigError(f"question {q.id!r}: grading {q.grading!r} not in {sorted(GRADINGS)}")
    bad_claims = set(q.claim_refs) - CLAIMS
    if bad_claims:
        raise ConfigError(f"question {q.id!r}: claim_refs {sorted(bad_claims)} not in {sorted(CLAIMS)}")
    if not q.question or not q.question.strip():
        raise ConfigError(f"question {q.id!r}: question text is empty")
    tokens = _TOKEN_RE.findall(q.question)
    leaked = sorted({tok.upper() for tok in tokens} & _LEAKAGE_JARGON_UPPER)
    leaked += sorted(tok for tok in tokens if tok in LEAKAGE_EDGE_WORDS)
    if leaked:
        raise ConfigError(
            f"question {q.id!r}: question text leaks tool vocabulary {leaked}; "
            "rephrase in an engineer's voice without jrag terms."
        )


_QUESTION_KEYS = {"id", "corpus", "category", "difficulty", "question",
                  "oracle_source", "claim_refs", "grading", "expected", "oracle_params"}


def _record_from_obj(obj: dict) -> Question:
    unknown = set(obj.keys()) - _QUESTION_KEYS
    if unknown:
        raise ConfigError(f"question record has unknown keys {sorted(unknown)}: {obj!r}")
    try:
        return Question(
            id=str(obj["id"]),
            corpus=str(obj["corpus"]),
            category=str(obj["category"]),
            difficulty=str(obj["difficulty"]),
            question=str(obj["question"]),
            oracle_source=str(obj["oracle_source"]),
            claim_refs=list(obj.get("claim_refs") or []),
            grading=str(obj["grading"]),
            expected=obj.get("expected"),
            oracle_params=dict(obj.get("oracle_params") or {}),
        )
    except KeyError as e:
        raise ConfigError(f"question record missing required field {e}") from e


def load_questions(
    path: str, *, valid_corpora: set[str] | None = None
) -> list[Question]:
    """Load one JSONL file -> validated, file-unique ``Question`` list."""
    records: list[Question] = []
    seen: set[str] = set()
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        obj = __json_loads(line, path, lineno)
        q = _record_from_obj(obj)
        validate(q, valid_corpora=valid_corpora)
        if q.id in seen:
            raise ConfigError(f"{path}:{lineno}: duplicate question id {q.id!r}")
        seen.add(q.id)
        records.append(q)
    return records


def __json_loads(line: str, path: str, lineno: int) -> dict:
    import json

    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}:{lineno}: invalid JSON ({e.msg})") from e
    if not isinstance(obj, dict):
        raise ConfigError(f"{path}:{lineno}: expected a JSON object")
    return obj


def load_all_questions(
    glob: str = "bench/questions/*.jsonl",
    *,
    valid_corpora: set[str] | None = None,
    corpora_path: str = "bench/corpora.yml",
) -> list[Question]:
    """Load every JSONL file matching ``glob`` -> globally-unique Question list."""
    if valid_corpora is None:
        valid_corpora = _corpora_names(corpora_path)
    seen: set[str] = set()
    out: list[Question] = []
    for path in sorted(_glob.glob(glob)):
        for q in load_questions(path, valid_corpora=valid_corpora):
            if q.id in seen:
                raise ConfigError(f"duplicate question id {q.id!r} across files (in {path})")
            seen.add(q.id)
            out.append(q)
    return out
