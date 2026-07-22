# Benchmark — MCP → CLI Surface Reframe (Plan 4)

- **Date:** 2026-07-22
- **Status:** Active (design approved, implemented; full CLI run pending)
- **Supersedes (surface only):** the MCP channel of Plans 1–3. Plan 3's
  methodology code (κ fix, wall-clock timeout, atomic writes, `report.py`,
  deterministic CI smoke) is **kept**; only the agent↔jrag channel moves.

## Motivation

The benchmark drove the agent through the **MCP** surface (`claude -p
--mcp-config bench/mcp/jrag.json`; conditions B/D allow/deny the five
`mcp__jrag__*` tools). MCP is the **legacy** surface. The product ships and
recommends the **`jrag` CLI** (`skills/explore-codebase-cli`,
`agents/explorer-rag-cli.md`, `docs/JRAG-CLI.md`); `install`/`update` expose
`--surface {mcp,cli}` with CLI primary. The benchmark was measuring the wrong
surface.

This is a **surface swap, not a science change.** Both surfaces drive the same
backend (`mcp_v2.py`: `resolve/find/describe/neighbors/search_v2`), so the
pre-registered claims **C1–C6 are surface-agnostic and unchanged** — A/B/C/D
keep their intent; only B and D's jrag channel moves from MCP tools to CLI
verbs. The CLI is ergonomically richer: D's high-level verbs
(`flow`/`decompose`/`impact`/`connection`/`overview`) compose multi-hop walks in
one call that MCP forces the agent to build by hand via repeated `neighbors`, so
the reframe is expected to *strengthen* the C2 (steps/tokens) story.

## Decisions locked

1. **Forward-only reframe.** New Plan 4 spec+plan; rewrite the *active* harness
   (conditions, loader, runner setup, prompts, blinding, tests); PREREG
   amendment; supersede MCP results. Plans 1/2/3 docs stay as merged history
   (pointer notes added to the active ones).
2. **Keep A/B/C/D.** No new conditions, no claim changes, no question/oracle/
   corpus changes.
3. **Discard/supersede MCP results.** Stop the in-flight MCP run; archive MCP
   smoke results under `bench/results/_superseded-mcp/`; CLI is the sole surface.
   (MCP-vs-CLI comparison deferred to a possible later plan.)

## The channel swap

| Concern | MCP (old) | CLI (new) |
|---|---|---|
| Agent channel | `mcp__jrag__{search,find,describe,neighbors,resolve}` | `jrag <verb>` via **Bash** |
| Per-cell setup | `materialize_mcp_config` → `.mcp.json`; `--mcp-config`/`--strict-mcp-config` | per-condition **PATH shim** + `JAVA_CODEBASE_RAG_INDEX_DIR`/`JAVA_CODEBASE_RAG_SOURCE_ROOT` env |
| Verb isolation (B vs D) | allow/deny MCP tool *names* | shim allow-list (B: `["search"]`; D: `JRAG_QUERY_VERBS`) |
| Lexical isolation (B) | deny `Grep`+`Glob` (clean — no Bash) | deny `Grep`+`Glob` + granular `Bash(<lexical> *)` denies |
| Blinding | scrub `mcp__jrag__\w+` | also scrub `jrag <verb>` |

## Isolation design (the crux)

Verified against Claude Code's permission model: granular `--disallowedTools`
rules like `Bash(grep *)` **are enforced** under `--permission-mode
bypassPermissions` (only *allow* rules are additive/non-restrictive there), and
compound commands are split on `&&`/`||`/`;`/`|` so `jrag search x && grep y` is
independently denied on the grep half. Three layers, consistent with the
already-accepted condition-C Bash caveat (PREREG Amendment 2026-07-21 (a)):

1. **Verb-level — PATH shim (OS-enforced).** `materialize_cli_env` writes
   `<cell>/bin/jrag` (a Python shim with the venv-python shebang) onto the spawn
   `PATH`; it exec's the real `.venv/bin/jrag` only for the condition's
   allow-list (B: `search`; D: all) and exits 2 otherwise. Allow-list, not
   deny-list → a future new verb can't leak into B.
2. **Lexical escape — granular Bash deny-list.** Condition B denies `Grep`/
   `Glob` plus `JRAG_LEXICAL_DENY` (`Bash(cat *)`/`Bash(grep *)`/…), appended by
   `to_flags`. `ESCAPE_TOOLS` is now auto-appended to every condition by
   `to_flags` (no longer hand-listed in `conditions.yml`).
3. **Residual leak — measured.** `report.py` counts lexical-command Bash calls
   per condition from transcripts and reports a per-condition leakage rate
   ("Lexical leakage" section). The residual is reported data, not a claim.
   (`--permission-mode dontAsk` with `Bash(jrag:*)` would give true allowlist
   isolation but changes the permission architecture the harness validated
   under headless `-p` — rejected alternative, noted in PREREG.)

## Files changed

**Harness:** `load_conditions.py` (CLI model: `JRAG_QUERY_VERBS`,
`JRAG_LEXICAL_DENY`, `jrag_allowed_verbs`; `to_flags` auto-appends ESCAPE + B's
lexical deny; `validate` retooled), `conditions.yml` (drop `mcp_servers`; add
`jrag_allowed_verbs`), `claude_runner.py` (`materialize_cli_env` +
`_resolve_real_jrag` replace `materialize_mcp_config`; `build_argv(spec, flags)`
drops `--mcp-config`; `run_cell` writes the shim + sets PATH/JRAG env), delete
`bench/mcp/jrag.json`.

**Prompts:** `prompts/D_jrag_full.md` (teach the `jrag` CLI surface, embedding
the `explore-codebase-cli` decision framework), `prompts/B_vector_only.md`
(`jrag search` only). Shared preamble byte-identical.

**Grading/reporting:** `grade.py` `TOOL_NAME_RE` scrubs `jrag <verb>`; `report.py`
adds the lexical-leakage metric (`run_dir` threaded into `render_report_markdown`).

**Tests:** `test_load_conditions.py` (rewritten for the CLI model),
`test_claude_runner.py` (shim materialization + gating; argv; run_cell
shim/env), `test_smoke_pipeline.py` + `test_run_bench.py` (drop `mcp_servers`).

**Docs:** `PREREGISTRATION.md` Amendment 2026-07-22 (Plan 4); `PHASE0_FINDINGS.md`
forward pointer; `README.md` rewrite; pointer notes on the Plan 3 + original
active specs.

## Out of scope

- MCP server code (`src/java_codebase_rag/mcp/`) and MCP product docs
  (`docs/AGENT-GUIDE.md`) — untouched; MCP remains a shipped surface.
- No new conditions, no claim/question/oracle/corpus changes.
- MCP-vs-CLI comparison (kept MCP run) — deferred.

## Verification

`rm -rf tests/*/.java-codebase-rag*`; `.venv/bin/pytest tests/bench/ -q` green
(126/127; the one failure is the live-API `test_judge_answer_returns_grade`,
environmental — unrelated to the reframe). Shim isolation spot-check (B rejects
graph verbs, allows `search`; grep/cat denied under B). Blinding spot-check (a D
transcript has no `jrag`/`mcp__jrag__` tokens after blinding). The full
~1,200-cell CLI run + report is the post-session deliverable.

## TL;DR

Reframe the effectiveness benchmark from legacy MCP to the shipped `jrag` CLI,
keeping A/B/C/D and claims C1–C6 (same backend). Swap the channel:
`--mcp-config`/`mcp__jrag__*` → a per-condition PATH shim (B = `search` only; D
= all verbs) + granular `Bash(<lexical> *)` denies under the existing
`bypassPermissions` mode, with a measured lexical-leakage metric. Rewrite the
loader/conditions/runner/prompts/blinding/tests; delete `bench/mcp/jrag.json`;
PREREG amendment + Plan 4 docs; MCP results superseded. Plan 3 methodology is
kept. The CLI's richer verbs should strengthen the C2 story.
