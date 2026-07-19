"""Tests for ``bench.oracle.calibration`` — mechanical-vs-manual gate."""
from __future__ import annotations

from dataclasses import dataclass

from bench.oracle import calibration as cal
from bench.oracle.build_oracle import (
    expected_client_route_pairs,
    expected_path,
    expected_symbol_set,
)


@dataclass
class _Q:
    id: str
    category: str
    oracle_source: str = "manual"
    corpus: str = "c"


def _report(agree_a: int, disagree_a: int, agree_b: int, disagree_b: int, threshold=0.9):
    """Build mechanical/manual dicts with controlled agreement per category."""
    questions = []
    mechanical, manual = {}, {}
    for cat, agree, disagree in (("role-listing", agree_a, disagree_a),
                                 ("interface-impls", agree_b, disagree_b)):
        for i in range(agree):
            qid = f"{cat}-agree-{i}"
            questions.append(_Q(qid, cat))
            e = expected_symbol_set([f"x.{cat}.{i}"])
            mechanical[qid] = e
            manual[qid] = e  # identical -> match
        for i in range(disagree):
            qid = f"{cat}-disagree-{i}"
            questions.append(_Q(qid, cat))
            mechanical[qid] = expected_symbol_set([f"x.{cat}.mech.{i}"])
            manual[qid] = expected_symbol_set([f"x.{cat}.man.{i}"])  # different -> mismatch
    return cal._calibrate_from_expected(questions, mechanical, manual, threshold)


def test_passes_above_threshold():
    # 9/10 and 10/10 -> overall 19/20 = 0.95, both categories >= 0.9.
    r = _report(agree_a=9, disagree_a=1, agree_b=10, disagree_b=0)
    assert r.passed is True
    assert r.overall.match == 19
    assert r.overall.total == 20
    assert abs(r.overall.ratio - 0.95) < 1e-9
    assert r.per_category["role-listing"].ratio == 0.9
    assert r.per_category["interface-impls"].ratio == 1.0


def test_fails_below_threshold():
    # 7/10 in one category -> ratio 0.7 < 0.9.
    r = _report(agree_a=7, disagree_a=3, agree_b=10, disagree_b=0)
    assert r.passed is False
    failing = r.failing_categories()
    assert "role-listing" in failing


def test_path_kind_uses_ordered_equality():
    q = _Q("q-path", "call-trace")
    mechanical = expected_path(["A", "B", "C"])
    manual = expected_path(["A", "C", "B"])  # same set, different order
    r = cal._calibrate_from_expected([q], {"q-path": mechanical}, {"q-path": manual}, 0.9)
    assert r.overall.match == 0  # ordered inequality -> mismatch
    assert r.overall.total == 1


def test_client_route_pairs_set_equality():
    q = _Q("q-cs", "cross-service")
    mechanical = expected_client_route_pairs(
        [{"client_fqn": "C", "route": "/x", "target_service": "s"}]
    )
    manual = expected_client_route_pairs(
        [{"client_fqn": "C", "route": "/x", "target_service": "s"}]
    )
    r = cal._calibrate_from_expected([q], {"q-cs": mechanical}, {"q-cs": manual}, 0.9)
    assert r.overall.match == 1
