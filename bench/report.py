"""Aggregate a graded benchmark run into a report (markdown + csv + plots).

Consumes one run dir's ``graded.jsonl`` (each line = a CellResult + ``grade``)
and emits ``report.md`` (condition x category tables, cross-service C3, per
model-tier x condition C6 deltas, judge<->human kappa, headline numbers, and a
"reproduce in 3 commands" block), ``results.csv`` (one row per graded cell), and
optional PNG plots. ``report.py`` recomputes kappa from ``human_labels.json`` when
that sibling file is present, so the report is self-contained.

Plotting is matplotlib, gracefully optional: tables + csv always emit; plots emit
only when matplotlib imports, else a one-line warning.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

CONDITION_ORDER = ["A", "B", "C", "D"]


# --- loading & enrichment ---


def load_graded_cells(graded_path: str) -> list[dict]:
    """Read a ``graded.jsonl`` into a list of cell dicts.

    Blank lines and unparseable trailing lines (a write-as-you-go crash
    leftover) are skipped.
    """
    cells: list[dict] = []
    for line in Path(graded_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            cells.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return cells


def attach_categories(cells: list[dict], questions_glob: str) -> list[dict]:
    """Attach ``category`` to each cell from the question registry (best-effort).

    If the registry can't be loaded or a question_id isn't found, the cell gets
    ``"unknown"``. Never raises — reporting must not depend on the registry.
    """
    qid_to_cat: dict[str, str] = {}
    try:
        from bench.load_questions import load_all_questions

        for q in load_all_questions(questions_glob):
            qid_to_cat[q.id] = q.category
    except Exception as exc:
        # Surface this — a silent "unknown" would degrade every category table
        # without any signal. Most common cause: invoking ``python bench/report.py``
        # directly (which puts ``bench/`` — not the repo root — on sys.path, so
        # ``bench.load_questions`` isn't importable). Use ``python -m bench.report``.
        if cells:
            print(
                f"warning: could not load question registry ({exc!r}); "
                f"categories will be 'unknown'. Invoke via 'python -m bench.report'.",
                file=sys.stderr,
            )
    for c in cells:
        c["category"] = qid_to_cat.get(c.get("question_id", ""), "unknown")
    return cells


# --- accessors (tolerant of missing/partial fields) ---


def _correctness(cell: dict) -> float:
    g = cell.get("grade") or {}
    return float(g.get("correctness") or 0.0)


def _tokens_total(cell: dict) -> int:
    t = cell.get("tokens") or {}
    return int(t.get("total") or 0)


def _n_turns(cell: dict) -> int:
    return int(cell.get("n_turns") or 0)


def _context(cell: dict) -> int:
    return int(cell.get("context_bytes_retrieved") or 0)


def _mean(values: list[float]) -> float:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def _sort_conditions(keys) -> list[str]:
    return sorted(
        keys,
        key=lambda k: CONDITION_ORDER.index(k)
        if k in CONDITION_ORDER
        else len(CONDITION_ORDER),
    )


# --- aggregations ---


def condition_means(cells: list[dict]) -> dict[str, float]:
    """Mean correctness per condition."""
    by: dict[str, list[float]] = defaultdict(list)
    for c in cells:
        by[c.get("condition", "?")].append(_correctness(c))
    return {k: _mean(v) for k, v in by.items()}


def aggregate_condition_category(cells: list[dict]) -> dict:
    """{(condition, category): {n, correctness, n_turns, tokens, context}} lists."""
    bucket: dict = defaultdict(
        lambda: {
            "n": 0,
            "correctness": [],
            "n_turns": [],
            "tokens": [],
            "context": [],
        }
    )
    for c in cells:
        key = (c.get("condition", "?"), c.get("category", "unknown"))
        b = bucket[key]
        b["n"] += 1
        b["correctness"].append(_correctness(c))
        b["n_turns"].append(_n_turns(c))
        b["tokens"].append(_tokens_total(c))
        b["context"].append(_context(c))
    return bucket


def model_condition_delta(cells: list[dict]) -> dict:
    """{(model, condition): mean correctness} for the C6 per-tier gap view."""
    bucket: dict = defaultdict(list)
    for c in cells:
        bucket[(c.get("model", "?"), c.get("condition", "?"))].append(
            _correctness(c)
        )
    return {k: _mean(v) for k, v in bucket.items()}


def _exit_counts(cells: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for c in cells:
        counts[c.get("exit_reason", "?")] += 1
    return dict(counts)


def _compute_kappa(cells: list[dict], run_dir: str):
    """Recompute judge<->human kappa from a sibling human_labels.json, or None.

    Pairs each judged cell whose run_id is labeled. Reuses bench.grade's
    ``_grade_to_judge_label`` (JUDGE_CORRECT_THRESHOLD) + ``cohen_kappa`` so the
    report's kappa matches what grading reports. None if no labels / too few.
    """
    hl_path = os.path.join(run_dir, "human_labels.json")
    if not os.path.exists(hl_path):
        return None
    try:
        from bench.grade import Grade, _grade_to_judge_label, cohen_kappa

        human = json.loads(Path(hl_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    judge_labels: list = []
    paired_human: list = []
    for c in cells:
        rid = c.get("run_id")
        g = c.get("grade")
        if rid in human and g:
            grade = Grade(
                correctness=float(g.get("correctness") or 0.0),
                method=g.get("method", ""),
                detail={},
                judge_model=g.get("judge_model"),
            )
            judge_labels.append(_grade_to_judge_label(grade))
            paired_human.append(human[rid])
    if len(judge_labels) < 2:
        return None
    try:
        return cohen_kappa(judge_labels, paired_human)
    except Exception:
        return None


# --- rendering ---


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def render_report_markdown(cells: list[dict], *, kappa=None) -> str:
    lines: list[str] = ["# Benchmark report", ""]
    n = len(cells)
    lines.append(f"**Cells graded:** {n}")
    if n:
        ec = _exit_counts(cells)
        caps = ec.get("cap", 0) + ec.get("timeout", 0)
        lines.append(
            f"**Answered (done):** {ec.get('done', 0)}  "
            f"**Caps/timeouts:** {caps}  **Errors:** {ec.get('error', 0)}"
        )
    lines.append("")

    if n == 0:
        lines.append("_No graded cells in this run._")
        return "\n".join(lines) + "\n"

    # Headline: per-condition mean correctness.
    cm = condition_means(cells)
    lines += ["## Headline — mean correctness by condition", ""]
    lines += ["| Condition | Mean correctness | n |", "|---|---|---|"]
    for cond in _sort_conditions(cm):
        cnt = sum(1 for c in cells if c.get("condition") == cond)
        lines.append(f"| {cond} | {_fmt(cm[cond])} | {cnt} |")
    lines.append("")

    # condition x category correctness.
    bucket = aggregate_condition_category(cells)
    conds = _sort_conditions({c for (c, _) in bucket})
    categories = sorted({cat for (_, cat) in bucket})
    lines += ["## Correctness by condition × category", ""]
    lines += ["| Category | " + " | ".join(conds) + " |"]
    lines += ["|---|" + "|".join(["---"] * len(conds)) + "|"]
    for cat in categories:
        row = [cat]
        for cond in conds:
            b = bucket.get((cond, cat))
            row.append(_fmt(_mean(b["correctness"])) if b else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # efficiency by condition.
    lines += [
        "## Efficiency by condition (mean steps / tokens / context bytes)",
        "",
        "| Condition | Mean steps | Mean tokens | Mean context bytes | n |",
        "|---|---|---|---|---|",
    ]
    eff: dict = defaultdict(
        lambda: {"n_turns": [], "tokens": [], "context": []}
    )
    for c in cells:
        e = eff[c.get("condition", "?")]
        e["n_turns"].append(_n_turns(c))
        e["tokens"].append(_tokens_total(c))
        e["context"].append(_context(c))
    for cond in _sort_conditions(eff):
        e = eff[cond]
        lines.append(
            f"| {cond} | {_mean(e['n_turns']):.1f} | {_mean(e['tokens']):.0f} | "
            f"{_mean(e['context']):.0f} | {len(e['n_turns'])} |"
        )
    lines.append("")

    # cross-service (C3).
    cs = [
        (cond, _mean(bucket[(cond, "cross-service")]["correctness"]))
        for cond in conds
        if (cond, "cross-service") in bucket
    ]
    if cs:
        lines += ["## Cross-service correctness (C3)", "", "| Condition | Mean |", "|---|---|"]
        lines += [f"| {cond} | {_fmt(m)} |" for cond, m in cs]
        lines.append("")

    # per model-tier x condition (C6 directional).
    mcd = model_condition_delta(cells)
    if len({m for (m, _) in mcd}) > 1:
        models = sorted({m for (m, _) in mcd})
        cconds = _sort_conditions({c for (_, c) in mcd})
        lines += ["## Per model-tier × condition (C6 directional)", ""]
        lines += ["| Model | " + " | ".join(cconds) + " |"]
        lines += ["|---|" + "|".join(["---"] * len(cconds)) + "|"]
        for m in models:
            row = [m] + [_fmt(mcd.get((m, cond), 0.0)) for cond in cconds]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # kappa.
    k = "N/A" if kappa is None else f"{kappa:.3f}"
    lines += [
        "## Inter-rater κ (judge ↔ human)",
        "",
        f"Cohen's κ: **{k}**",
        "",
    ]

    lines += [
        "## Reproduce",
        "",
        "```",
        "# 1. run the grid (full)",
        ".venv/bin/python -m bench.run_bench --models glm-4.7,glm-5.1 "
        "--seeds 0,1,2 --max-turns 30 --wall-timeout 900",
        "# 2. grade",
        ".venv/bin/python -m bench.grade "
        "--cells bench/results/<run>/cells.jsonl "
        "--human-labels bench/results/<run>/human_labels.json",
        "# 3. report",
        ".venv/bin/python -m bench.report --run-dir bench/results/<run>",
        "```",
        "",
    ]
    return "\n".join(lines)


CSV_COLUMNS = [
    "run_id",
    "question_id",
    "corpus",
    "condition",
    "model",
    "seed",
    "category",
    "exit_reason",
    "n_turns",
    "n_tool_calls",
    "context_bytes_retrieved",
    "tokens_total",
    "wall_s",
    "correctness",
    "grade_method",
]


def write_results_csv(cells: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for c in cells:
            g = c.get("grade") or {}
            w.writerow(
                [
                    c.get("run_id", ""),
                    c.get("question_id", ""),
                    c.get("corpus", ""),
                    c.get("condition", ""),
                    c.get("model", ""),
                    c.get("seed", ""),
                    c.get("category", "unknown"),
                    c.get("exit_reason", ""),
                    _n_turns(c),
                    c.get("n_tool_calls", ""),
                    _context(c),
                    _tokens_total(c),
                    c.get("wall_s", ""),
                    g.get("correctness", ""),
                    g.get("method", ""),
                ]
            )


# --- plotting (matplotlib, gracefully optional) ---
#
# Validated categorical palette per the dataviz skill (references/palette.md,
# light mode). Conditions use fixed slot order A/B/C/D — the ordering is the
# CVD-safety mechanism, not cosmetic. Validator result (4 hues, light surface):
#   CVD worst-adjacent ΔE 24.2 (>= 12 target), all in lightness band, chroma ok.
#   Contrast WARN on aqua/yellow is relieved by the legend + axis labels here
#   PLUS the markdown/CSV table views in the report (a WARN obligates visible
#   labels or a table — both present). Dark mode is a stretch (re-validate vs
#   surface #1a1a19).
_CHART_SURFACE = "#fcfcfb"
_TEXT_PRIMARY = "#0b0b0b"
_TEXT_SECONDARY = "#52514e"
_GRID = "#e6e5e1"
_COND_COLORS = {
    "A": "#2a78d6",  # slot 1 blue
    "B": "#1baf7a",  # slot 2 aqua
    "C": "#eda100",  # slot 3 yellow
    "D": "#008300",  # slot 4 green
}
_FALLBACK_COLOR = "#52514e"


def _cond_color(cond: str) -> str:
    return _COND_COLORS.get(cond, _FALLBACK_COLOR)


def _style_axes(fig, ax) -> None:
    """Recessive grid/axes + text-in-ink (dataviz mark spec)."""
    fig.patch.set_facecolor(_CHART_SURFACE)
    ax.set_facecolor(_CHART_SURFACE)
    for spine in ax.spines.values():
        spine.set_color(_TEXT_SECONDARY)
        spine.set_linewidth(0.8)
    ax.tick_params(colors=_TEXT_SECONDARY, labelsize=9)
    ax.title.set_color(_TEXT_PRIMARY)
    ax.xaxis.label.set_color(_TEXT_SECONDARY)
    ax.yaxis.label.set_color(_TEXT_SECONDARY)
    ax.yaxis.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def render_plots(cells: list[dict], out_dir: str) -> list[str]:
    """Render PNG plots if matplotlib is importable; else warn and return []."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "warning: matplotlib not installed; skipping plots "
            "(install with: pip install matplotlib)",
            file=sys.stderr,
        )
        return []
    return _render_plots(cells, out_dir, plt)


def _render_plots(cells, out_dir, plt) -> list[str]:
    paths: list[str] = []
    if not cells:
        return paths

    conds = _sort_conditions({c.get("condition", "?") for c in cells})

    # 1. correctness by category, grouped by condition (categorical bar).
    bucket = aggregate_condition_category(cells)
    categories = sorted({cat for (_, cat) in bucket})
    fig, ax = plt.subplots(figsize=(8, 4.5))
    group_w = 0.8
    bar_w = group_w / max(1, len(conds))
    gap = 0.02  # 2px-ish surface gap between adjacent bars within a group
    for i, cond in enumerate(conds):
        xs = [x + i * bar_w for x in range(len(categories))]
        ys = [
            _mean(bucket[(cond, cat)]["correctness"])
            if (cond, cat) in bucket
            else 0.0
            for cat in categories
        ]
        ax.bar(
            xs, ys, bar_w - gap, label=f"Cond {cond}", color=_cond_color(cond)
        )
    ax.set_xticks(
        [x + (len(conds) - 1) * bar_w / 2 for x in range(len(categories))]
    )
    ax.set_xticklabels(categories, rotation=30, ha="right")
    ax.set_ylabel("Mean correctness")
    ax.set_title("Correctness by category × condition")
    ax.set_ylim(0, 1.05)
    ax.legend(ncol=len(conds), framealpha=0.0)
    _style_axes(fig, ax)
    fig.tight_layout()
    p = os.path.join(out_dir, "plot_correctness_by_category.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)

    # 2. correctness vs tokens (scatter) — the quality-per-cost headline.
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for cond in conds:
        xs = [_tokens_total(c) for c in cells if c.get("condition") == cond]
        ys = [_correctness(c) for c in cells if c.get("condition") == cond]
        ax.scatter(
            xs, ys, s=55, color=_cond_color(cond), label=f"Cond {cond}", alpha=0.75,
            edgecolors="none",
        )
    ax.set_xlabel("Total tokens")
    ax.set_ylabel("Correctness")
    ax.set_ylim(-0.05, 1.05)
    ax.set_title("Correctness vs tokens (quality per unit cost)")
    ax.legend(framealpha=0.0)
    _style_axes(fig, ax)
    fig.tight_layout()
    p = os.path.join(out_dir, "plot_correctness_vs_tokens.png")
    fig.savefig(p, dpi=130)
    plt.close(fig)
    paths.append(p)

    # 3. steps-to-answer by condition (boxplot).
    present = [cond for cond in conds if any(c.get("condition") == cond for c in cells)]
    data = [[_n_turns(c) for c in cells if c.get("condition") == cond] for cond in present]
    data = [d for d in data if d]
    if data:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        bp = ax.boxplot(
            data,
            tick_labels=present[: len(data)],
            patch_artist=True,
            widths=0.6,
        )
        for patch, cond in zip(bp["boxes"], present[: len(data)]):
            patch.set_facecolor(_cond_color(cond))
            patch.set_alpha(0.55)
            patch.set_edgecolor(_cond_color(cond))
        for element in ("whiskers", "caps", "medians"):
            for line in bp[element]:
                line.set_color(_TEXT_PRIMARY)
        for flier in bp["fliers"]:
            flier.set_markeredgecolor(_TEXT_SECONDARY)
        ax.set_ylabel("Steps to answer (n_turns)")
        ax.set_xlabel("Condition")
        ax.set_title("Steps to answer by condition")
        _style_axes(fig, ax)
        fig.tight_layout()
        p = os.path.join(out_dir, "plot_steps_by_condition.png")
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)

    return paths


# --- CLI ---


def report_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="report")
    parser.add_argument(
        "--run-dir", required=True, help="Run dir containing graded.jsonl"
    )
    parser.add_argument(
        "--out", default=None, help="Output dir (default: the run dir)"
    )
    parser.add_argument(
        "--questions-glob", default="bench/questions/*.jsonl"
    )
    parser.add_argument(
        "--no-plots", action="store_true", help="Skip PNG plot generation"
    )
    args = parser.parse_args(argv)

    run_dir = args.run_dir
    out_dir = args.out or run_dir
    os.makedirs(out_dir, exist_ok=True)

    graded = os.path.join(run_dir, "graded.jsonl")
    cells = load_graded_cells(graded)
    cells = attach_categories(cells, args.questions_glob)

    kappa = _compute_kappa(cells, run_dir)

    report_md = render_report_markdown(cells, kappa=kappa)
    Path(os.path.join(out_dir, "report.md")).write_text(
        report_md, encoding="utf-8"
    )
    write_results_csv(cells, os.path.join(out_dir, "results.csv"))

    if not args.no_plots:
        render_plots(cells, out_dir)

    print(
        f"report: {len(cells)} cells -> {os.path.join(out_dir, 'report.md')} "
        f"+ results.csv"
        + ("" if args.no_plots else " + plots")
    )
    return 0


if __name__ == "__main__":
    sys.exit(report_main())
