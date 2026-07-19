"""Oracle merge pipeline (Plan 1, Task 9).

Merges three independent sources into the frozen ``expected/<id>.json`` ground
truth: jqassistant (mechanical, Cypher), jdeps (mechanical, JDK-native), and a
manual expert file. Runners are dependency-injected so tests can mock them
without touching jqassistant/jdeps.

``Question`` is duck-typed (a ``Protocol``): the real ``bench.load_questions.Question``
(Task 11) satisfies it, but this module does not import that module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from bench.oracle import jdeps_runner, jqa_runner

# category -> Expected.kind. Lets the oracle shape rows without the question's
# expected being pre-filled (expected is None until this pipeline fills it).
CATEGORY_KIND: dict[str, str] = {
    "interface-impls": "symbol_set",
    "upstream-consumers": "symbol_set",
    "call-trace": "path",
    "blast-radius": "symbol_set",
    "cross-service": "client_route_pairs",
    "role-listing": "symbol_set",
    "semantic": "symbol_set",
    "absence": "absence",
}


class OracleError(ValueError):
    """Raised on an unknown/unparseable oracle_source or a runner failure."""


# --- Expected payload constructors (plain dicts for direct JSON serialization). ---


def expected_symbol_set(fqns: list[str], ids: list[str] | None = None) -> dict:
    return {"kind": "symbol_set", "fqns": sorted(set(fqns)), "ids": ids or []}


def expected_path(hops: list[str | dict]) -> dict:
    normalized = [{"fqn": h} if isinstance(h, str) else h for h in hops]
    return {"kind": "path", "hops": normalized}


def expected_client_route_pairs(pairs: list[dict]) -> dict:
    return {"kind": "client_route_pairs", "pairs": pairs}


def expected_absence(proof: str) -> dict:
    return {"kind": "absence", "verdict": "not_in_project", "proof": proof}


class Question(Protocol):  # structural; satisfied by bench.load_questions.Question
    id: str
    category: str
    oracle_source: str


@dataclass
class Manifest:
    per_category: dict[str, int]
    per_source: dict[str, int]
    total: int

    def to_dict(self) -> dict:
        return {
            "per_category": self.per_category,
            "per_source": self.per_source,
            "total": self.total,
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shape(kind: str, rows: list[dict]) -> dict:
    if not rows and kind != "symbol_set":
        # Allow empty symbol_set (valid: nothing implements); other kinds need rows.
        pass
    if kind == "symbol_set":
        fqns = []
        for row in rows:
            if row:
                fqns.append(next(iter(row.values())))
        return expected_symbol_set(fqns)
    if kind == "path":
        hops = [next(iter(row.values())) for row in rows if row]
        return expected_path(hops)
    if kind == "client_route_pairs":
        return expected_client_route_pairs(rows)
    raise OracleError(f"no jqassistant shaping rule for kind {kind!r}")


def _engine_of(oracle_source: str) -> str:
    return oracle_source.split(":", 1)[0] if ":" in oracle_source else oracle_source


def _derive(
    q, corpus_checkout: str, rules_dir: str, classpath_root: str | None,
    manual_qs: dict, jqa_run, jdeps_run,
) -> dict:
    src = q.oracle_source
    if src.startswith("jqassistant:"):
        rule_name = src.split(":", 1)[1]
        rule_path = Path(rules_dir) / rule_name
        params = getattr(q, "oracle_params", None) or {}
        rows = jqa_run(corpus_checkout, rule_path, params)
        kind = CATEGORY_KIND.get(q.category)
        if kind is None:
            raise OracleError(f"question {q.id}: no Expected kind for category {q.category!r}")
        return _shape(kind, rows)
    if src == "jdeps":
        if not classpath_root:
            raise OracleError(f"question {q.id}: oracle_source 'jdeps' requires classpath_root")
        pairs = jdeps_run(classpath_root)
        # dependency side as the symbol set.
        return expected_symbol_set([dep for _, dep in pairs])
    if src == "manual":
        entry = manual_qs.get(q.id)
        if not entry or "expected" not in entry:
            raise OracleError(f"question {q.id}: manual source missing expected for {q.id!r}")
        return entry["expected"]
    raise OracleError(f"question {q.id}: unknown oracle_source {src!r}")


def build_expected(
    corpus_checkout: str,
    questions: list,
    rules_dir: str,
    classpath_root: str | None,
    manual_path: str,
    out_dir: str,
    *,
    jqa_run=None,
    jdeps_run=None,
) -> Manifest:
    """Merge jqassistant + jdeps + manual -> ``out_dir/<id>.json`` + manifest."""
    jqa_run = jqa_run or jqa_runner.run_rule
    jdeps_run = jdeps_run or jdeps_runner.run

    manual_qs: dict = {}
    if manual_path and Path(manual_path).is_file():
        manual_qs = json.loads(Path(manual_path).read_text(encoding="utf-8")).get("questions", {})

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    per_category: dict[str, int] = {}
    per_source: dict[str, int] = {}
    total = 0
    for q in questions:
        expected = _derive(
            q, corpus_checkout, rules_dir, classpath_root, manual_qs, jqa_run, jdeps_run
        )
        record = {
            "question_id": q.id,
            "expected": expected,
            "oracle_source": q.oracle_source,
            "derived_at": _now(),
        }
        (out / f"{q.id}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        per_category[q.category] = per_category.get(q.category, 0) + 1
        engine = _engine_of(q.oracle_source)
        per_source[engine] = per_source.get(engine, 0) + 1
        total += 1

    manifest = Manifest(per_category=per_category, per_source=per_source, total=total)
    manifest.write(out / "_manifest.json")
    return manifest


# --- CLI (Task 15 uses --corpus / --calibrate). ---


def _load_questions_for_corpus(corpus: str) -> list:
    from bench.load_questions import load_all_questions

    return [q for q in load_all_questions() if q.corpus == corpus]


def _main() -> int:  # pragma: no cover - exercised in Task 15
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--corpus", required=True)
    p.add_argument("--out", default="bench/oracle/expected")
    p.add_argument("--calibrate", action="store_true")
    args = p.parse_args()

    from bench.load_corpora import load_corpora

    corpora = {c.name: c for c in load_corpora()}
    rec = corpora[args.corpus]
    questions = _load_questions_for_corpus(args.corpus)
    manual = f"bench/oracle/manual/{args.corpus}.json"

    build_expected(
        corpus_checkout=rec.checkout_path,
        questions=questions,
        rules_dir="bench/oracle/jqassistant_rules",
        classpath_root=None,
        manual_path=manual,
        out_dir=args.out,
    )
    if args.calibrate:
        from bench.oracle.calibration import calibrate

        report = calibrate(
            corpus_checkout=rec.checkout_path,
            questions=questions,
            rules_dir="bench/oracle/jqassistant_rules",
            classpath_root=None,
            manual_path=manual,
        )
        report.write(Path("bench/oracle/calibration_report.json"))
        return 0 if report.passed else 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
