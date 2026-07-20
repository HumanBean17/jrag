"""Tests for ``bench.load_questions`` — schema + anti-leakage validator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.load_questions import (
    CATEGORIES,
    CLAIMS,
    LEAKAGE_VOCAB,
    ConfigError,
    Question,
    load_all_questions,
    load_questions,
)

VALID_CORPORA = {"bank-chat-system", "shopizer", "spring-petclinic-microservices"}


def _line(**over) -> str:
    base = dict(
        id="bc-impl-01",
        corpus="bank-chat-system",
        category="interface-impls",
        difficulty="easy",
        question="Which classes implement the AssignStrategy interface?",
        expected=None,
        oracle_source="jqassistant:implements.cypher",
        claim_refs=["C1"],
        grading="programmatic_set_match",
    )
    base.update(over)
    return json.dumps(base)


def _write_jsonl(tmp_path: Path, name: str, lines: list[str]) -> str:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_loads_valid_question(tmp_path):
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line()])
    qs = load_questions(path, valid_corpora=VALID_CORPORA)
    assert len(qs) == 1
    q = qs[0]
    assert isinstance(q, Question)
    assert q.id == "bc-impl-01"
    assert q.corpus == "bank-chat-system"
    assert q.category == "interface-impls"
    assert q.expected is None
    assert q.claim_refs == ["C1"]


def test_rejects_leakage(tmp_path):
    # Engineer would never write "IMPLEMENTS" (the tool's vocabulary).
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(question="which classes IMPLEMENTS the interface")])
    with pytest.raises(ConfigError) as exc:
        load_questions(path, valid_corpora=VALID_CORPORA)
    assert "IMPLEMENTS" in str(exc.value)


def test_rejects_leakage_lowercase_tool_term(tmp_path):
    # "neighbors" is a jrag tool name; using it leaks vocabulary.
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(question="Use the neighbors tool to find callers")])
    with pytest.raises(ConfigError) as exc:
        load_questions(path, valid_corpora=VALID_CORPORA)
    assert "neighbors" in str(exc.value)


def test_rejects_bad_category(tmp_path):
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(category="vibes")])
    with pytest.raises(ConfigError):
        load_questions(path, valid_corpora=VALID_CORPORA)


def test_rejects_bad_claim_ref(tmp_path):
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(claim_refs=["C9"])])
    with pytest.raises(ConfigError):
        load_questions(path, valid_corpora=VALID_CORPORA)


def test_rejects_unknown_corpus(tmp_path):
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(corpus="not-a-corpus")])
    with pytest.raises(ConfigError):
        load_questions(path, valid_corpora=VALID_CORPORA)


def test_rejects_dup_id_within_file(tmp_path):
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(), _line(id="bc-impl-02", question="second")])
    # both lines share... no, ids differ here. Force a dup:
    path = _write_jsonl(tmp_path, "bc.jsonl", [_line(), _line(question="dup id same")])
    with pytest.raises(ConfigError):
        load_questions(path, valid_corpora=VALID_CORPORA)


def test_rejects_dup_id_across_files(tmp_path):
    _write_jsonl(tmp_path, "a.jsonl", [_line()])
    _write_jsonl(tmp_path, "b.jsonl", [_line(question="dup across files")])
    with pytest.raises(ConfigError):
        load_all_questions(
            glob=str(tmp_path / "*.jsonl"),
            valid_corpora=VALID_CORPORA,
        )


def test_constants_closed():
    assert CATEGORIES == {
        "interface-impls", "upstream-consumers", "call-trace", "blast-radius",
        "cross-service", "role-listing", "semantic", "absence",
    }
    assert CLAIMS == {"C1", "C2", "C3", "C4", "C5", "C6"}
    assert "neighbors" in LEAKAGE_VOCAB and "ontology_version" in LEAKAGE_VOCAB


def test_rejects_jargon_case_variants(tmp_path):
    # Pure-jargon terms (no natural-English use) are matched case-insensitively,
    # so a capitalized "MCP__Jrag" / "Ontology_Version" / "Edge_Types" is still a leak.
    for leaky in ("via MCP__Jrag lookup", "read the Ontology_Version", "Edge_Types everywhere"):
        path = _write_jsonl(tmp_path, "bc.jsonl", [_line(question=leaky)])
        with pytest.raises(ConfigError):
            load_questions(path, valid_corpora=VALID_CORPORA)


def test_allows_natural_english_not_edge_casing(tmp_path):
    # "calls" (natural English) is NOT the uppercase `:CALLS` edge — it must pass.
    # This is the design point: case-sensitivity on edge words avoids false
    # positives on ordinary prose (cross-service questions phrase "calls" this way).
    path = _write_jsonl(
        tmp_path, "bc.jsonl",
        [_line(question="Which service calls the notification endpoint directly?")],
    )
    load_questions(path, valid_corpora=VALID_CORPORA)  # must not raise


def test_rejects_unknown_key(tmp_path):
    obj = json.loads(_line())
    obj["rationale"] = "should not be allowed"
    path = _write_jsonl(tmp_path, "bc.jsonl", [json.dumps(obj)])
    with pytest.raises(ConfigError) as exc:
        load_questions(path, valid_corpora=VALID_CORPORA)
    assert "rationale" in str(exc.value)
