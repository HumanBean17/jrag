"""Tests for bench/report.py — aggregate a graded run to markdown + csv + plots."""

import json

import pytest

from bench.report import (
    condition_means,
    report_main,
    render_plots,
    write_results_csv,
)


def _graded_cell(
    run_id="bc-impl-01_D_glm-4.7_s0",
    *,
    question_id="bc-impl-01",
    condition="D",
    model="glm-4.7",
    correctness=1.0,
    method="set_match",
    n_turns=8,
    tokens_total=150,
    context_bytes=1000,
    exit_reason="done",
    wall_s=10.0,
):
    """Build one minimal graded.jsonl line as a dict."""
    return {
        "run_id": run_id,
        "question_id": question_id,
        "corpus": "bank-chat-system",
        "condition": condition,
        "model": model,
        "seed": 0,
        "exit_reason": exit_reason,
        "n_turns": n_turns,
        "n_tool_calls": 3,
        "context_bytes_retrieved": context_bytes,
        "tokens": {"input": 100, "output": 50, "total": tokens_total},
        "wall_s": wall_s,
        "grade": {
            "correctness": correctness,
            "method": method,
            "detail": {},
            "judge_model": None,
        },
    }


def _write_graded(path, cells):
    path.write_text("\n".join(json.dumps(c) for c in cells) + "\n")


def test_count_lexical_leak_detects_lexical_bash(tmp_path):
    """A Bash tool_use invoking a lexical binary counts as a leak; a jrag call does not."""
    from bench.report import _count_lexical_leak

    leaky = tmp_path / "leak.jsonl"
    leaky.write_text(
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Bash","input":{"command":"grep -rn Foo src/"}}]}}\n'
    )
    clean = tmp_path / "clean.jsonl"
    clean.write_text(
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"name":"Bash","input":{"command":"jrag search Foo"}}]}}\n'
    )
    missing = tmp_path / "absent.jsonl"
    assert _count_lexical_leak(str(leaky)) == 1
    assert _count_lexical_leak(str(clean)) == 0
    assert _count_lexical_leak(str(missing)) == 0  # OSError -> 0


def test_lexical_command_re_matches_common_binaries():
    """Regression for the parse bug: the detector must match grep/find/cat/rg.

    A prior ``.rstrip(" *")`` left the trailing ')' so every JRAG_LEXICAL_DENY
    entry parsed as e.g. "cat *)" and the regex matched nothing (leakage=0.00).
    """
    from bench.report import _lexical_command_re

    rx = _lexical_command_re()
    for cmd in ("grep foo", "find . -name x", "cat file", "rg pattern", "head -5 f"):
        assert rx.search(cmd), cmd
    assert rx.search("jrag x && grep y")  # after a separator
    assert not rx.search("jrag search foo")  # jrag itself is not lexical


def test_lexical_leakage_by_condition_rate_and_n(tmp_path):
    """Returns (fraction, n-with-transcript); cells without a transcript are excluded."""
    from bench.report import _lexical_leakage_by_condition

    run_dir = tmp_path
    for rid, leak in (("b1", True), ("b2", False)):
        d = run_dir / rid
        d.mkdir()
        cmd = "grep foo" if leak else "jrag search foo"
        (d / "transcript.jsonl").write_text(
            '{"type":"assistant","message":{"content":[{"type":"tool_use",'
            '"name":"Bash","input":{"command":"%s"}}]}}\n' % cmd
        )
    cells = [
        {"run_id": "b1", "condition": "B"},
        {"run_id": "b2", "condition": "B"},
        {"run_id": "d1", "condition": "D"},  # no transcript dir -> excluded
    ]
    out = _lexical_leakage_by_condition(cells, str(run_dir))
    assert out["B"] == (0.5, 2)  # 1 of 2 findable B transcripts leaked
    assert "D" not in out  # d1 had no findable transcript -> not counted in n


def test_report_aggregates_condition_means(tmp_path):
    """report_main writes report.md whose condition means match the data.

    Two D cells at correctness 1.0, two A cells at 0.0 -> D mean 1.0 > A mean 0.0.
    Also covers results.csv emission and the helper directly.
    """
    cells = [
        _graded_cell("d1", condition="D", correctness=1.0),
        _graded_cell("d2", condition="D", correctness=1.0),
        _graded_cell("a1", condition="A", correctness=0.0),
        _graded_cell("a2", condition="A", correctness=0.0),
    ]
    _write_graded(tmp_path / "graded.jsonl", cells)

    rc = report_main(["--run-dir", str(tmp_path)])
    assert rc == 0

    report = (tmp_path / "report.md").read_text()
    # Headline condition means appear.
    means = condition_means(cells)
    assert means["D"] == 1.0
    assert means["A"] == 0.0
    assert "1.00" in report  # D mean rendered
    # results.csv exists and has a header + one row per cell.
    csv_text = (tmp_path / "results.csv").read_text()
    csv_lines = [ln for ln in csv_text.strip().splitlines() if ln]
    assert len(csv_lines) == len(cells) + 1  # header + rows


def test_report_csv_one_row_per_cell(tmp_path):
    """write_results_csv emits exactly one row per graded cell + a header."""
    cells = [
        _graded_cell("c1", condition="A"),
        _graded_cell("c2", condition="B"),
        _graded_cell("c3", condition="D"),
    ]
    out = tmp_path / "results.csv"
    write_results_csv(cells, str(out))
    lines = [ln for ln in out.read_text().strip().splitlines() if ln]
    assert len(lines) == 4  # header + 3
    assert "condition" in lines[0]
    assert "correctness" in lines[0]


def test_report_handles_empty_run(tmp_path):
    """An empty graded.jsonl produces a report without crashing (no cells)."""
    (tmp_path / "graded.jsonl").write_text("")
    rc = report_main(["--run-dir", str(tmp_path)])
    assert rc == 0
    report = (tmp_path / "report.md").read_text()
    assert "no graded cells" in report.lower() or "0" in report
    # CSV has only a header.
    csv_lines = [
        ln for ln in (tmp_path / "results.csv").read_text().splitlines() if ln
    ]
    assert len(csv_lines) == 1


def test_report_render_plots(tmp_path):
    """render_plots emits PNGs when matplotlib is present (skips if absent)."""
    pytest.importorskip("matplotlib")
    cells = [
        _graded_cell("d1", condition="D", correctness=1.0, tokens_total=100, n_turns=5),
        _graded_cell("d2", condition="D", correctness=0.8, tokens_total=200, n_turns=7),
        _graded_cell("a1", condition="A", correctness=0.3, tokens_total=400, n_turns=12),
        _graded_cell("a2", condition="A", correctness=0.4, tokens_total=500, n_turns=14),
    ]
    paths = render_plots(cells, str(tmp_path))
    assert paths, "expected at least one plot PNG"
    import os

    for p in paths:
        assert p.endswith(".png")
        assert os.path.exists(p)
