> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: reprocess selective phase rebuild (`--vectors-only` / `--graph-only`)

Status: **completed — shipped** to `master`. This plan implemented
[`propose/completed/REPROCESS-SPLIT-PROPOSE.md`](../propose/completed/REPROCESS-SPLIT-PROPOSE.md).

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
| No-flag path | Continue using `server.run_refresh_pipeline(...)` to preserve orchestration; add `phases_run` for phases actually spawned (`[]` for setup failure, `["vectors"]` if cocoindex ran and graph did not, `["vectors","graph"]` if graph spawned). |
| Selective path wiring | Use `java_codebase_rag.pipeline.run_cocoindex_update` and `run_build_ast_graph` directly from `cli.py`; skip lazy `import server` for selective paths. |
| Drift communication | Emit one stderr line after successful partial run; `--quiet` does not suppress this warning. |
| Exit mapping rewrite | Replace current `return 2 if payload.get("exit_code") is None else 1` logic with phase-spawn-aware mapping: setup / usage / phase-never-spawned failures exit `2`; requested build failures exit `1`. |
| Pretty output | Match the propose: when stdout is a TTY, include `Rebuilt:` / `Skipped:` bullets for selective runs while preserving JSON output when piped. |

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
  - emit payload with `phases_run` reflecting actually spawned phases, not merely
    requested phases;
  - print partial-run drift warning to stderr only after successful selective run;
  - rewrite final CLI return mapping so graph-only non-zero builder exit returns `1`, not `2`;
  - explicitly map helper-level setup failures (`returncode` `126` / `127`, missing
    binary / missing bundled script) to exit `2` with `phases_run=[]`;
  - add a small reprocess pretty renderer so TTY output shows `Rebuilt:` and
    `Skipped:` for selective runs without changing piped JSON.
- Keep `refresh` alias behavior unchanged (still warns + rewrites to `reprocess`).

### 2. `server.py`

- Extend `RefreshIndexOutput` model with additive `phases_run` field.
- Populate `phases_run` from actual no-flag execution:
  - `[]` for early setup failures before cocoindex spawns;
  - `["vectors"]` after cocoindex spawns, including cocoindex build failure before
    graph is attempted;
  - `["vectors","graph"]` after graph builder spawns, including graph build failure.

### 3. `docs/JAVA-CODEBASE-RAG-CLI.md`

- Update `reprocess` section with new selective invocations and warning behavior.
- Document mutually-exclusive usage error shape and expected exit behavior for
  selective build failures vs preflight failures.
- Document the actual argparse stderr shape emitted by this CLI. Do not promise a
  full `usage:` block unless the implementation changes `main()` to print one for
  `argparse.ArgumentError`.

### 4. `README.md`

- Update lifecycle command summary row for `reprocess` to mention selective flags.
- Sweep the README for operator-facing statements that currently equate
  `reprocess` with "full Lance + full Kuzu" and qualify them as default/no-flag
  behavior where needed.
- Keep "re-index required" guidance clear: full `reprocess` remains the safe
  coherence operation after ontology changes; selective flags are for known
  one-store invalidations.

### 5. `tests/test_java_codebase_rag_cli.py`

- Add focused CLI tests for selective reprocess:
  1. `test_reprocess_vectors_only_skips_graph`
  2. `test_reprocess_graph_only_skips_vectors`
  3. `test_reprocess_mutually_exclusive_flags_rejected`
  4. `test_reprocess_graph_only_build_failure_returns_exit_1`
  5. `test_reprocess_vectors_only_emits_graph_stale_warning`
  6. `test_reprocess_graph_only_emits_vectors_stale_warning`
  7. `test_reprocess_vectors_only_setup_failure_returns_exit_2_without_phase`
  8. `test_reprocess_graph_only_setup_failure_returns_exit_2_without_phase`
  9. `test_reprocess_no_flag_cocoindex_failure_records_vectors_only`
  10. `test_reprocess_pretty_output_lists_rebuilt_and_skipped`
- Preserve and keep green existing integration anchor:
  - `test_cli_reprocess_builds_kuzu_path` (no-flag regression coverage).

## Tests for PR-RS1

1. `test_reprocess_vectors_only_skips_graph`
2. `test_reprocess_graph_only_skips_vectors`
3. `test_reprocess_mutually_exclusive_flags_rejected`
4. `test_reprocess_graph_only_build_failure_returns_exit_1`
5. `test_reprocess_vectors_only_emits_graph_stale_warning`
6. `test_reprocess_graph_only_emits_vectors_stale_warning`
7. `test_reprocess_vectors_only_setup_failure_returns_exit_2_without_phase`
8. `test_reprocess_graph_only_setup_failure_returns_exit_2_without_phase`
9. `test_reprocess_no_flag_cocoindex_failure_records_vectors_only`
10. `test_reprocess_pretty_output_lists_rebuilt_and_skipped`
11. `test_cli_reprocess_builds_kuzu_path` (existing regression anchor)

## Definition of done (PR-RS1)

- [x] `reprocess --help` shows mutually-exclusive selective flags.
- [x] `--vectors-only` runs only cocoindex full reprocess path and never invokes graph builder.
- [x] `--graph-only` runs only graph builder and never invokes cocoindex.
- [x] Selective success emits one drift warning to stderr naming the non-rebuilt store.
- [x] JSON payload includes `phases_run` with values matching phases actually spawned.
- [x] Graph-only build failure exits with code `1` (not `2`).
- [x] Selective setup failures exit with code `2`, emit no drift warning, and report
      `phases_run=[]`.
- [x] No-flag cocoindex failure reports `phases_run=["vectors"]` because graph was
      not spawned.
- [x] TTY pretty output for selective success includes `Rebuilt:` and `Skipped:`.
- [x] No-flag `reprocess` lifecycle test remains green.
- [x] `.venv/bin/python -m pytest tests/test_java_codebase_rag_cli.py -q` passes.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add parser flags + update help text | `java_codebase_rag/cli.py` | `reprocess --help` shows `[--vectors-only | --graph-only]` |
| 2 | Refactor `_cmd_reprocess` flow split | `java_codebase_rag/cli.py` | Three explicit modes work with correct payload, setup/build exit mapping, drift warnings, and selective pretty output |
| 3 | Extend refresh payload model | `server.py` | `RefreshIndexOutput` serializes `phases_run` consistently |
| 4 | Add/adjust CLI docs | `docs/JAVA-CODEBASE-RAG-CLI.md`, `README.md` | Docs match new CLI behavior, warning policy, argparse error shape, and default-vs-selective wording |
| 5 | Add selective CLI tests | `tests/test_java_codebase_rag_cli.py` | New tests pass locally and anchor exit-code semantics |

All rows landed on `master` (PR-RS1).

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Exit code regression for graph-only failure (`None` mistakenly mapped to `2`) | High | Add dedicated failing-graph selective test and branch-aware exit logic. |
| 2 | JSON consumer ambiguity when `graph_exit_code` or `exit_code` is `None` | Medium | Require `phases_run` in payload and document consumer branching pattern. |
| 3 | Selective paths accidentally pull heavy `server` imports | Medium | Keep selective branches on lightweight `pipeline` helpers only. |
| 4 | Drift warning becomes noisy or suppressible unexpectedly | Low | Emit exactly one deterministic line and keep independent from `--quiet`. |
| 5 | Help/docs mismatch with parser behavior | Low | Update parser description and CLI docs in same PR with usage test coverage. |
| 6 | Direct helper setup failures (`126` / `127`) are mislabeled as build failures | High | Add setup-failure tests for both selective modes and map phase-never-spawned to exit `2` with `phases_run=[]`. |
| 7 | Propose / plan mismatch on TTY pretty output | Medium | Implement the propose's `Rebuilt:` / `Skipped:` selective pretty output and test it directly. |

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
3. Payload contract is additive (`phases_run`) and documented for operators,
   including actual-spawned semantics for failures.
4. CLI docs and README lifecycle references reflect selective reprocess behavior
   and keep no-flag `reprocess` as the recommended coherence operation.
5. CLI test suite includes selective coverage and keeps existing no-flag
   regression anchor green.

# Tracking

- `PR-RS1`: **landed** on `master` (selective `reprocess`, `phases_run`, docs, CLI tests).
