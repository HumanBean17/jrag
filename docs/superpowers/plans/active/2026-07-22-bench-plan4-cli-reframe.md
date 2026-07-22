# Plan 4 — MCP → CLI Surface Reframe (task breakdown)

- **Date:** 2026-07-22
- **Design:** `docs/superpowers/specs/active/2026-07-22-bench-plan4-cli-reframe-design.md`
- **Status:** implemented; full CLI run pending.

Surface swap only — Plan 3 methodology (κ, timeout, atomic writes, `report.py`,
CI smoke) is kept. Each task is RED→GREEN against `tests/bench/` (fake-claude /
monkeypatch fixtures; no paid API except the pre-existing
`test_judge_answer_returns_grade`).

## Task 1 — CLI condition model (`bench/load_conditions.py`)
Replace `mcp_servers`/`JRAG_*_TOOLS`/`mcp__jrag__*` with: `JRAG_QUERY_VERBS`
(full agent verb set minus `watch`/`vocab-index`), `JRAG_SEARCH_VERBS`,
`JRAG_LEXICAL_DENY` (granular `Bash(<lexical> *)` list). `Condition` gains
`jrag_allowed_verbs` (default `None`); `to_flags` auto-appends `ESCAPE_TOOLS`
(always) + `JRAG_LEXICAL_DENY` (B only); `validate` retooled (A/C none; B ==
`["search"]` + Grep/Glob denied; D ⊇ `JRAG_QUERY_VERBS`; no condition allows an
escape tool). `_resolve_verbs` maps the YAML `all` sentinel → `JRAG_QUERY_VERBS`.

## Task 2 — `bench/conditions.yml`
Drop `mcp_servers`. A: lexical (unchanged intent). B: `jrag_allowed_verbs:
[search]`, allow `Read`+`Bash`, deny `Grep`+`Glob`. C: raw agent (deny `Grep`).
D: `jrag_allowed_verbs: all`, allow `Read`+`Grep`+`Glob`+`Bash`.

## Task 3 — CLI channel in `bench/claude_runner.py`
`materialize_cli_env(cell_dir, allowed_verbs, real_jrag_bin, venv_python)` writes
`<cell>/bin/jrag` (Python shim; venv-python shebang; allow-list via a proper
tuple literal; exec's real for allowed verbs + `--help`/`-h`, else exit 2) and
returns the shim dir. `_resolve_real_jrag` prefers `<venv>/bin/jrag` else
`shutil.which`. `build_argv(spec, flags)` drops `--mcp-config`. `run_cell` drops
`jrag_mcp_template`, gains `jrag_bin`; for jrag conditions it writes the shim,
prepends it to `PATH`, sets `JAVA_CODEBASE_RAG_INDEX_DIR`/`…_SOURCE_ROOT`, and
passes `env=` to `Popen`.

## Task 4 — Prompts (`bench/prompts/{B,D}_*.md`)
D: teach the `jrag` CLI surface (locate/resolve, traverse, high-level
compositions, entry points, orient) + Read/Grep/Glob; embed the
`explore-codebase-cli` decision framework. B: `jrag search` only; no graph, no
Grep/Glob/shell text tools. Shared preamble byte-identical (asserted in tests).

## Task 5 — Blinding (`bench/grade.py`)
`TOOL_NAME_RE` scrubs `jrag(?:\s+[a-z][\w-]*)?` (the verb too, so B vs D is
hidden) + legacy `mcp__jrag__\w+` + built-in tools. Docstrings updated.

## Task 6 — Leakage metric (`bench/report.py`)
`_count_lexical_leak` / `_lexical_leakage_by_condition` (reuses
`JRAG_LEXICAL_DENY`); new "Lexical leakage (isolation fidelity)" section;
`run_dir` threaded into `render_report_markdown`.

## Task 7 — Delete `bench/mcp/jrag.json`; update tests
Delete the template. Rewrite `test_load_conditions.py` (CLI invariants),
`test_claude_runner.py` (shim write/gate, argv, run_cell shim+env), drop
`mcp_servers` from `test_smoke_pipeline.py` + `test_run_bench.py`. Extend
`emit_short.sh` to record env (`JRAG_ENV_SIDECAR`).

## Task 8 — Docs
`PREREGISTRATION.md` Amendment 2026-07-22 (Plan 4); `PHASE0_FINDINGS.md` forward
pointer; `README.md` rewrite; this spec+plan; pointer notes on Plan 3 spec/plan
+ the original `2026-07-12-benchmark-effectiveness-design.md`.

## Task 9 — Supersede MCP results
Move MCP smoke runs + the in-flight full run to `bench/results/_superseded-mcp/`
(+ README); gitignore `bench/results/`.

## Task 10 — Verify (post-session)
`pytest tests/bench/` green (done: 126/127; 1 environmental live-API test). Shim
isolation spot-check. Blinding spot-check. Full ~1,200-cell CLI run + report
(delivers the C1–C6 verdicts).

## TL;DR
Ten tasks: swap the jrag channel MCP→CLI across the loader, conditions, runner
(PATH shim), prompts, blinding, and tests; add a leakage metric; reframe the
docs and supersede MCP results. Claims C1–C6 unchanged (same backend).
