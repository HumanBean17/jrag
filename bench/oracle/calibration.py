"""Calibration gate (Plan 1, Task 10).

The mechanical oracle (jqassistant + jdeps) is not trusted on the large corpora
until it agrees with the manual expert on bank-chat. ``calibrate`` builds the
mechanical expected answers, diffs them against the manual truth question by
question (exact set / ordered-path / verdict equality by ``kind``), and tallies
agreement per category and overall.

``passed`` requires every category AND the overall ratio to meet ``threshold``
(default 0.9). The threshold is never lowered to make a failing gate pass.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bench.oracle import build_oracle


@dataclass
class Agreement:
    match: int
    total: int
    ratio: float = field(init=False)

    def __post_init__(self) -> None:
        self.ratio = (self.match / self.total) if self.total else 1.0


@dataclass
class CalibrationReport:
    per_category: dict[str, Agreement]
    overall: Agreement
    threshold: float
    passed: bool

    def failing_categories(self) -> list[str]:
        return [c for c, a in self.per_category.items() if a.ratio < self.threshold]

    def to_dict(self) -> dict:
        return {
            "per_category": {c: {"match": a.match, "total": a.total, "ratio": a.ratio}
                             for c, a in self.per_category.items()},
            "overall": {"match": self.overall.match, "total": self.overall.total,
                        "ratio": self.overall.ratio},
            "threshold": self.threshold,
            "passed": self.passed,
            "failing_categories": self.failing_categories(),
        }

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def _pair_key(p: dict) -> tuple:
    return (p.get("client_fqn"), p.get("route"), p.get("target_service"))


def _expected_equal(mech: dict, man: dict) -> bool:
    if mech.get("kind") != man.get("kind"):
        return False
    kind = mech["kind"]
    if kind == "symbol_set":
        return set(mech.get("fqns", [])) == set(man.get("fqns", []))
    if kind == "path":
        # Ordered equality — call-trace order matters.
        m = [h.get("fqn") for h in mech.get("hops", [])]
        n = [h.get("fqn") for h in man.get("hops", [])]
        return m == n
    if kind == "client_route_pairs":
        return {_pair_key(p) for p in mech.get("pairs", [])} == {
            _pair_key(p) for p in man.get("pairs", [])
        }
    if kind == "absence":
        return mech.get("verdict") == man.get("verdict")
    return False


def _calibrate_from_expected(
    questions: list,
    mechanical: dict[str, dict],
    manual: dict[str, dict],
    threshold: float = 0.9,
) -> CalibrationReport:
    per_cat_match: dict[str, int] = {}
    per_cat_total: dict[str, int] = {}
    overall_match = 0
    overall_total = 0
    for q in questions:
        if q.id not in mechanical or q.id not in manual:
            continue  # only questions both sources answer count
        overall_total += 1
        per_cat_total[q.category] = per_cat_total.get(q.category, 0) + 1
        if _expected_equal(mechanical[q.id], manual[q.id]):
            overall_match += 1
            per_cat_match[q.category] = per_cat_match.get(q.category, 0) + 1

    per_category = {
        cat: Agreement(match=per_cat_match.get(cat, 0), total=per_cat_total[cat])
        for cat in per_cat_total
    }
    overall = Agreement(match=overall_match, total=overall_total)
    passed = bool(per_category) and all(a.ratio >= threshold for a in per_category.values()) \
        and overall.ratio >= threshold
    return CalibrationReport(per_category=per_category, overall=overall, threshold=threshold, passed=passed)


def _build_mechanical(corpus_checkout, questions, rules_dir, classpath_root,
                      build_fn, jqa_run, jdeps_run) -> dict[str, dict]:
    """Run the mechanical oracle and collect expected payloads keyed by id.

    Only mechanical-source questions (jqassistant/jdeps) are built — manual-source
    questions have no independent mechanical answer and are excluded from the
    calibration comparison (the plan: 'restricted to categories both cover').
    """
    out = Path(tempfile.mkdtemp(prefix="calibrate-"))
    build_fn = build_fn or build_oracle.build_expected
    mech_questions = [
        q for q in questions
        if q.oracle_source.startswith("jqassistant") or q.oracle_source == "jdeps"
    ]
    kwargs = dict(
        corpus_checkout=corpus_checkout, questions=mech_questions, rules_dir=rules_dir,
        classpath_root=classpath_root,
        manual_path="",  # mechanical build does not consult the manual file
        out_dir=str(out),
    )
    if jqa_run is not None:
        kwargs["jqa_run"] = jqa_run
    if jdeps_run is not None:
        kwargs["jdeps_run"] = jdeps_run
    build_fn(**kwargs)
    mechanical: dict[str, dict] = {}
    for f in out.glob("*.json"):
        if f.name == "_manifest.json":
            continue
        rec = json.loads(f.read_text(encoding="utf-8"))
        mechanical[rec["question_id"]] = rec["expected"]
    return mechanical


def calibrate(
    corpus_checkout: str,
    questions: list,
    rules_dir: str,
    classpath_root: str | None,
    manual_path: str,
    threshold: float = 0.9,
    *,
    mechanical: dict[str, dict] | None = None,
    build_fn=None,
    jqa_run=None,
    jdeps_run=None,
) -> CalibrationReport:
    """Build mechanical expected, diff vs manual truth -> CalibrationReport."""
    if mechanical is None:
        mechanical = _build_mechanical(
            corpus_checkout, questions, rules_dir, classpath_root, build_fn, jqa_run, jdeps_run
        )
    manual_qs: dict[str, dict] = {}
    if manual_path and Path(manual_path).is_file():
        raw = json.loads(Path(manual_path).read_text(encoding="utf-8")).get("questions", {})
        manual_qs = {qid: v["expected"] for qid, v in raw.items() if "expected" in v}
    return _calibrate_from_expected(questions, mechanical, manual_qs, threshold)
