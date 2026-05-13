# Plan: reprocess selective phase rebuild (`--vectors-only` / `--graph-only`)

Status: **active (planning)**. This plan implements
[`propose/REPROCESS-SPLIT-PROPOSE.md`](../propose/REPROCESS-SPLIT-PROPOSE.md).

Depends on: **none**.

## Goal

- Add selective rebuild control to `java-codebase-rag reprocess` via
  mutually-exclusive flags: `--vectors-only` and `--graph-only`.
- Keep no-flag behavior aligned to current lifecycle semantics: vectors first,
  graph second, with no new subcommands.
- Make partial rebuilds explicit and operable by emitting a drift warning on
  stderr while preserving a binary success/failure exit contract.
- Extend JSON output additively with `phases_run` so automation can
  disambiguate "skipped" vs "not executed due to failure".

## Principles (do not relitigate in review)

- **Single verb:** scope stays on `reprocess`; no new lifecycle command names.
- **Default path stability:** `reprocess` with no selective flag still runs both
  phases in current order and remains the recommended coherence operation.
- **Strict mutual exclusion:** parser rejects `--vectors-only --graph-only`
  before any subprocess work.
- **Drift is warned, not blocked:** partial runs print one stderr warning naming
  the non-rebuilt store; no hard refusal and no drift-specific exit code.
- **Stage isolation:** vectors-only must not spawn graph builder; graph-only
  must not spawn cocoindex.
- **Exit semantics by requested phase:** exit `1` for any requested-phase build
  failure, exit `2` only for usage/setup failures (invalid args, missing binary,
  phase never spawned).

## PR breakdown - overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-RS1 | Add selective `reprocess` flags, payload field, drift warnings, docs, and CLI tests | none | 5-6 | CLI unit + lifecycle integration regression | yes |

Landing order: **RS1**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Flag shape | Use `--vectors-only` and `--graph-only` as argparse mutually-exclusive options on `reprocess`. |
| Output model | Add `phases_run: list[Literal["vectors","graph"]]` to `RefreshIndexOutput` (additive contract). |
| No-flag path | Continue using `server.run_refresh_pipeline(...)` to preserve orchestration; add `phases_run=["vectors","graph"]` in that path. |
| Selective path wiring | Use `java_codebase_rag.pipeline.run_cocoindex_update` and `run_build_ast_graph` directly from `cli.py`; skip lazy `import server` for selective paths. |
| Drift communication | Emit one stderr line after successful partial run; `--quiet` does not suppress this warning. |
| Exit mapping rewrite | Replace current `return 2 if payload.get("exit_code") is None else 1` logic with requested-phase-aware mapping. |
| Pretty output | Keep existing pretty/JSON auto-emission behavior; rely on payload + warning line without adding a new renderer mode. |

---

# PR-RS1 - selective reprocess phases and contract-safe output

## File-by-file changes

### 1. `java_codebase_rag/cli.py`

- Extend `reprocess` subparser:
  - add mutually-exclusive group with `--vectors-only` and `--graph-only`;
  - refresh description text to reflect full-or-selective behavior.
- Refactor `_cmd_reprocess(args)`:
  - branch into three explicit flows: default both, vectors-only, graph-only;
  - for vectors-only, run `run_cocoindex_update(..., full_reprocess=True, quiet=...)`;
  - for graph-only, run `run_build_ast_graph(..., verbose=not quiet, env=...)`;
  - emit payload with `phases_run` reflecting actually requested/ran phases;
  - print partial-run drift warning to stderr only after successful selective run;
  - rewrite final CLI return mapping so graph-only non-zero builder exit returns `1`, not `2`.
- Keep `refresh` alias behavior unchanged (still warns + rewrites to `reprocess`).

### 2. `server.py`

- Extend `RefreshIndexOutput` model with additive `phases_run` field.
- Populate `phases_run=["vectors","graph"]` on no-flag refresh path success/failure
  where phases are invoked (and `[]` for early pre-spawn failures where appropriate).

### 3. `docs/JAVA-CODEBASE-RAG-CLI.md`

- Update `reprocess` section with new selective invocations and warning behavior.
- Document mutually-exclusive usage error shape and expected exit behavior for
  selective build failures vs preflight failures.

### 4. `README.md`

- Update lifecycle command summary row for `reprocess` to mention selective flags.
- Keep wording consistent with operator guidance already referencing reprocess.

### 5. `tests/test_java_codebase_rag_cli.py`

- Add focused CLI tests for selective reprocess:
  1. `test_reprocess_vectors_only_skips_graph`
  2. `test_reprocess_graph_only_skips_vectors`
  3. `test_reprocess_mutually_exclusive_flags_rejected`
  4. `test_reprocess_graph_only_build_failure_returns_exit_1`
  5. `test_reprocess_vectors_only_emits_graph_stale_warning`
  6. `test_reprocess_graph_only_emits_vectors_stale_warning`
- Preserve and keep green existing integration anchor:
  - `test_cli_reprocess_builds_kuzu_path` (no-flag regression coverage).

## Tests for PR-RS1

1. `test_reprocess_vectors_only_skips_graph`
2. `test_reprocess_graph_only_skips_vectors`
3. `test_reprocess_mutually_exclusive_flags_rejected`
4. `test_reprocess_graph_only_build_failure_returns_exit_1`
5. `test_reprocess_vectors_only_emits_graph_stale_warning`
6. `test_reprocess_graph_only_emits_vectors_stale_warning`
7. `test_cli_reprocess_builds_kuzu_path` (existing regression anchor)

## Definition of done (PR-RS1)

- [ ] `reprocess --help` shows mutually-exclusive selective flags.
- [ ] `--vectors-only` runs only cocoindex full reprocess path and never invokes graph builder.
- [ ] `--graph-only` runs only graph builder and never invokes cocoindex.
- [ ] Selective success emits one drift warning to stderr naming the non-rebuilt store.
- [ ] JSON payload includes `phases_run` with values matching requested execution.
- [ ] Graph-only build failure exits with code `1` (not `2`).
- [ ] No-flag `reprocess` lifecycle test remains green.
- [ ] `python -m pytest tests/test_java_codebase_rag_cli.py -q` passes.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add parser flags + update help text | `java_codebase_rag/cli.py` | `reprocess --help` shows `[--vectors-only | --graph-only]` |
| 2 | Refactor `_cmd_reprocess` flow split | `java_codebase_rag/cli.py` | Three explicit modes work with correct payload + return mapping |
| 3 | Extend refresh payload model | `server.py` | `RefreshIndexOutput` serializes `phases_run` consistently |
| 4 | Add/adjust CLI docs | `docs/JAVA-CODEBASE-RAG-CLI.md`, `README.md` | Docs match new CLI behavior and warnings |
| 5 | Add selective CLI tests | `tests/test_java_codebase_rag_cli.py` | New tests pass locally and anchor exit-code semantics |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Exit code regression for graph-only failure (`None` mistakenly mapped to `2`) | High | Add dedicated failing-graph selective test and branch-aware exit logic. |
| 2 | JSON consumer ambiguity when `graph_exit_code` or `exit_code` is `None` | Medium | Require `phases_run` in payload and document consumer branching pattern. |
| 3 | Selective paths accidentally pull heavy `server` imports | Medium | Keep selective branches on lightweight `pipeline` helpers only. |
| 4 | Drift warning becomes noisy or suppressible unexpectedly | Low | Emit exactly one deterministic line and keep independent from `--quiet`. |
| 5 | Help/docs mismatch with parser behavior | Low | Update parser description and CLI docs in same PR with usage test coverage. |

# Out of scope

- Splitting `init` into selective modes.
- Splitting `increment` into selective modes.
- Introducing drift detection (`--detect-drift`) or a drift gate (`--allow-drift`).
- Parallelizing vectors and graph rebuild phases.
- Adding new lifecycle verbs or `--phases=<csv>` syntax.
- Changing MCP tool surface (`search`, `find`, `describe`, `neighbors`).

# Whole-plan done definition

1. Selective reprocess flags are available and mutually exclusive, with phase
   isolation enforced.
2. Exit semantics are correct for requested-phase failures and setup/usage errors.
3. Payload contract is additive (`phases_run`) and documented for operators.
4. CLI docs and README lifecycle summary reflect selective reprocess behavior.
5. CLI test suite includes selective coverage and keeps existing no-flag
   regression anchor green.

# Tracking

- `PR-RS1`: _pending_
