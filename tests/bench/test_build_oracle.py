"""Tests for ``bench.oracle.build_oracle`` — merge jqassistant + jdeps + manual."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from bench.oracle.build_oracle import (
    OracleError,
    build_expected,
    expected_absence,
    expected_symbol_set,
)


@dataclass
class _Q:
    id: str
    category: str
    oracle_source: str
    oracle_params: dict | None = None


def test_unknown_source_raises(tmp_path):
    with pytest.raises(OracleError):
        build_expected(
            corpus_checkout=str(tmp_path),
            questions=[_Q("q-x", "interface-impls", "astrology")],
            rules_dir=str(tmp_path),
            classpath_root=None,
            manual_path=str(tmp_path / "missing.json"),
            out_dir=str(tmp_path / "out"),
        )


def test_merges_jqa_and_manual(tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "implements.cypher").write_text("// stub\nRETURN 1 AS x;\n", encoding="utf-8")
    manual_path = tmp_path / "manual.json"
    manual_path.write_text(
        json.dumps(
            {
                "questions": {
                    "q-man": {
                        "expected": expected_absence("searched for X, none found"),
                        "rationale": "no matches",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    def fake_jqa(checkout, rule, params=None, **kw):
        assert rule.name == "implements.cypher"
        return [{"implementer_fqn": "x.Cat"}, {"implementer_fqn": "x.Dog"}]

    manifest = build_expected(
        corpus_checkout=str(tmp_path),
        questions=[
            _Q("q-jqa", "interface-impls", "jqassistant:implements.cypher"),
            _Q("q-man", "absence", "manual"),
        ],
        rules_dir=str(rules_dir),
        classpath_root=None,
        manual_path=str(manual_path),
        out_dir=str(out_dir),
        jqa_run=fake_jqa,
    )

    # Both per-question files written.
    jqa = json.loads((out_dir / "q-jqa.json").read_text())
    man = json.loads((out_dir / "q-man.json").read_text())

    assert jqa["question_id"] == "q-jqa"
    assert jqa["expected"]["kind"] == "symbol_set"
    assert set(jqa["expected"]["fqns"]) == {"x.Cat", "x.Dog"}
    assert jqa["oracle_source"] == "jqassistant:implements.cypher"

    assert man["expected"]["kind"] == "absence"
    assert man["expected"]["verdict"] == "not_in_project"

    # Manifest aggregation.
    assert manifest.total == 2
    assert manifest.per_source == {"jqassistant": 1, "manual": 1}
    assert manifest.per_category == {"interface-impls": 1, "absence": 1}

    written = json.loads((out_dir / "_manifest.json").read_text())
    assert written["total"] == 2
    assert written["per_source"] == {"jqassistant": 1, "manual": 1}


def test_symbol_set_helper():
    e = expected_symbol_set(["x.B", "x.A", "x.A"])
    assert e == {"kind": "symbol_set", "fqns": ["x.A", "x.B"], "ids": []}
