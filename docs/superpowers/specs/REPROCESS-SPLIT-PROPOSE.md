<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# REPROCESS-SPLIT — Make `reprocess` selectively rebuild vectors and/or graph

**Status**: completed — shipped (`reprocess --vectors-only` / `--graph-only`, `phases_run`, drift warnings, docs, and CLI tests on `master`).
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-12 (revised 2026-05-13 after review)

## TL;DR

- **The call**: add two mutually-exclusive flags to `java-codebase-rag reprocess` — `--vectors-only` and `--graph-only`. Default (no flag) is unchanged: rebuild both, in the current order (Lance via cocoindex, then Kuzu via `build_ast_graph.py`).
- **Why**: the two stores already live in separate subprocesses with zero shared in-process state, but the CLI forces operators to rebuild both even when only one is invalidated (e.g. edited `route_overrides` → only graph; switched `embedding.model` → only vectors). On large trees the wasted phase costs minutes.
- **Coherence**: a partial rebuild can leave Lance and Kuzu observing different source snapshots. Emit a one-line stderr WARN after every partial rebuild naming the unrefreshed store; no hard refusal.
- **Migration**: 1 PR. No-flag path keeps today's phase order and exit-code semantics; JSON output gains one additive field (`phases_run`). No new subcommands. ~2 new flags on the existing parser, ~15 LoC behind them in `_cmd_reprocess`, ~4 new tests.
- **Out of scope**: `init` and `increment` (the latter already rebuilds graph after a vector update; not worth splitting until use cases surface).

## 1. Frame: what is this thing, really?

> **`reprocess` is a coherence operation, not a single atomic write. It's "rebuild whichever side has drifted from the current source snapshot". Today it conflates 'rebuild' with 'rebuild *everything*'.**

The Lance pipeline (cocoindex update --full-reprocess) and the Kuzu graph builder (`build_ast_graph.py`) are already two independent subprocesses. They share inputs (source tree, ignore rules, microservice_roots) but not output stores or in-process state. The current CLI shape pretends they're one transaction; the underlying architecture says otherwise.

Reifying that separation costs almost nothing (the helper `run_build_ast_graph` already exists in `java_codebase_rag/pipeline.py`; the cocoindex invocation is already in `server.run_refresh_pipeline`). What we're proposing is exposing the existing factoring through two flags, not introducing new factoring.

## 2. Design principles

1. **Default phase semantics unchanged.** `java-codebase-rag reprocess` with no flags runs both phases in the existing order with the existing exit-code contract. The JSON output gains one additive field (`phases_run`, §3.3); consumers that ignore unknown fields are unaffected. Strict-schema validators may need to add `phases_run` to their allowed-keys list.
2. **Two flags, mutually exclusive.** `--vectors-only` and `--graph-only`. Passing both is a usage error; the parser rejects it before any subprocess spawns.
3. **Partial rebuilds warn, never refuse.** A one-line stderr hint after a partial rebuild names the store that was *not* rebuilt. No `--allow-drift` opt-in, no hard refusal. The operator's call.
4. **Stage isolation, not transactional coupling.** A vector-only rebuild must not touch `code_graph.kuzu`. A graph-only rebuild must not touch Lance tables or `cocoindex.db`. Each flag selects exactly one phase; the other phase doesn't run at all (not even a dry-run).
5. **One verb, no new verbs.** No `rebuild-graph`, no `rebuild-vectors`. Flags on `reprocess`.
6. **Exit code stays binary and meaningful.** Success = the *requested* phase(s) completed cleanly. A graph-only run returns 0 even though Lance was untouched. Drift is communicated via stderr WARN, not exit code.
7. **JSON output extends, not breaks.** Existing fields (`success`, `exit_code`, `stdout`, `stderr`, `graph_exit_code`, …) keep their meaning. Add one new field: `phases_run` — a list naming which phases ran (`["vectors", "graph"]`, `["vectors"]`, or `["graph"]`). Old JSON consumers that ignore unknown fields keep working.

## 3. The proposed surface

### 3.1 CLI

```
java-codebase-rag reprocess [--quiet]                 # both phases (today's behaviour)
java-codebase-rag reprocess [--quiet] --vectors-only  # cocoindex update --full-reprocess only
java-codebase-rag reprocess [--quiet] --graph-only    # build_ast_graph.py only
```

Mutually-exclusive flag pair. Argparse's `add_mutually_exclusive_group()` handles the validation:

```
$ java-codebase-rag reprocess --vectors-only --graph-only
usage: java-codebase-rag reprocess [-h] [--quiet] [--vectors-only | --graph-only]
java-codebase-rag reprocess: error: argument --graph-only: not allowed with argument --vectors-only
```

### 3.2 Stderr drift WARN

After every partial rebuild, **before** the JSON / pretty output is emitted, print one line to stderr:

```
# After --vectors-only:
java-codebase-rag reprocess: rebuilt vectors only; graph (code_graph.kuzu) was NOT rebuilt and may now reflect a stale source snapshot.

# After --graph-only:
java-codebase-rag reprocess: rebuilt graph only; vectors (Lance tables under <index_dir>) were NOT rebuilt and may now reflect a stale source snapshot.
```

The wording is deliberately neutral — "may" not "will" — because the partial rebuild is often the correct answer (see §4 use cases).

`--quiet` does **not** suppress the drift WARN. `--quiet` controls subprocess verbosity (`-q` to cocoindex, omitted `--verbose` to the graph builder); the one-line coherence hint is independent of that knob.

### 3.3 JSON output

`RefreshIndexOutput` gains one optional field:

```python
phases_run: list[Literal["vectors", "graph"]]
```

Values:
- both phases: `["vectors", "graph"]`
- `--vectors-only`: `["vectors"]`
- `--graph-only`: `["graph"]`

Existing fields (`success`, `exit_code`, `stdout`, `stderr`, `graph_exit_code`, `message`) keep their current semantics. Per-flag field matrix:

| Scenario | `success` | `exit_code` | `graph_exit_code` | `phases_run` | CLI exit |
|---|---|---|---|---|---|
| No flag, both phases succeed | True  | 0      | 0      | `["vectors","graph"]` | 0 |
| No flag, cocoindex fails     | False | != 0   | None   | `["vectors"]`         | 1 |
| No flag, graph fails         | False | 0      | != 0   | `["vectors","graph"]` | 1 |
| `--vectors-only` succeeds    | True  | 0      | None   | `["vectors"]`         | 0 |
| `--vectors-only` fails       | False | != 0   | None   | `["vectors"]`         | 1 |
| `--graph-only` succeeds      | True  | None   | 0      | `["graph"]`           | 0 |
| `--graph-only` fails         | False | None   | != 0   | `["graph"]`           | **1** (see below) |
| Missing cocoindex bin        | False | None   | None   | `[]`                  | 2 |
| Missing graph builder        | False | None   | None   | `[]`                  | 2 |

**Two things to note for migrators:**

1. **CLI exit-code mapping change.** Today's `_cmd_reprocess` ends with `return 2 if payload.get("exit_code") is None else 1` (`java_codebase_rag/cli.py:190`). That mapping treats `exit_code is None` as "phase never spawned" → exit 2. Under `--graph-only` with a graph-builder failure, `exit_code` is also `None` but the failure is a *build* failure, which today maps to exit 1 on the both-phase path. The PR must rewrite the mapping so exit 2 stays reserved for "phase never spawned" (missing binary, usage error) and exit 1 covers a build failure on any requested phase. This keeps exit codes meaningful for `$?`-branching CI scripts.

2. **`graph_exit_code = None` ambiguity.** Today `graph_exit_code = None` means "cocoindex failed, graph never ran". Under `--vectors-only` it also means "graph deliberately skipped". Same shape, two meanings. Old consumers can't disambiguate without reading `phases_run`. New consumers should branch on `phases_run` first, then on the per-phase exit code. The same shape applies symmetrically to `exit_code = None` under `--graph-only`.

### 3.4 What "skipped" looks like in pretty output

The pretty (TTY) renderer adds one bullet:

```
Rebuilt: graph
Skipped: vectors (use `java-codebase-rag reprocess --vectors-only` or `reprocess` to refresh)
```

## 4. Use-case re-walk

16 cases. Every row tells us: which flag (if any), what the operator expected, and which exit / WARN they should see.

| # | Use case | Flag | Phases run | Exit | Drift WARN? |
|---|---|---|---|---|---|
| UC1 | Default rebuild after big refactor across many files | (none) | vectors, graph | 0 / 1 | no |
| UC2 | Operator edited `route_overrides:` in YAML; wants graph rebuilt | `--graph-only` | graph | 0 / 1 | yes |
| UC3 | Operator switched `embedding.model` (hub-id swap); needs vector rebuild | `--vectors-only` | vectors | 0 / 1 | yes |
| UC4 | Operator added a `microservice_roots:` entry; graph rebuilds the microservice rollup (Lance flow does not consume `microservice_roots` — verified against `java_index_flow_lancedb.py`) | `--graph-only` | graph | 0 / 1 | yes |
| UC5 | Operator only changed Python prompts in agent skill; nothing should rebuild | n/a | none (don't run reprocess) | n/a | n/a (skill doc only) |
| UC6 | Graph builder crashed mid-run last time; vector store is fine; retry | `--graph-only` | graph | 0 | yes |
| UC7 | Lance write was interrupted; Kuzu is current; finish vectors | `--vectors-only` | vectors | 0 | yes |
| UC8 | Operator wants to A/B compare two `embedding.model` values | `--vectors-only` (twice, between hub-id swaps) | vectors | 0 | yes (each run) |
| UC9 | Operator tweaked an `@interface` declaration; graph annotation closure stale | `--graph-only` | graph | 0 | yes |
| UC10 | Dev iterates on `route_overrides` and wants only the graph to repick them up between edits | `--graph-only` | graph | 0 | yes |
| UC11 | Operator passes both flags by mistake | both | none (argparse rejects) | 2 | n/a |
| UC12 | Operator passes `--vectors-only` but cocoindex CLI is missing | `--vectors-only` | none (early fail) | 1 (or 127) | no (failure preempts WARN) |
| UC13 | Operator passes `--graph-only` but `build_ast_graph.py` is missing from the bundle | `--graph-only` | none (early fail) | 2 | no (failure preempts WARN) |
| UC14 | CI pipeline scripts call `reprocess` with no flags after a checkout | (none) | vectors, graph | 0 | no |
| UC15 | Operator wants a fast smoke test of vector indexing on a small tree | `--vectors-only` | vectors | 0 | yes (acceptable; one-off) |
| UC16 | `--graph-only` runs but graph builder exits non-zero (parser crash, Kuzu write error) | `--graph-only` | graph (failed) | **1** (not 2 — see §3.3 mapping note) | no (failure preempts WARN) |

**Gaps found in walk**: UC11 — argparse exit code for mutually-exclusive violations is 2 in stdlib. That's consistent with how the CLI already treats usage errors elsewhere. UC12 / UC13 confirm the "WARN" line must be emitted only when the requested phase *actually ran*; on early failure (missing binary) the existing failure path takes over and no drift WARN is emitted. UC16 forces the exit-code mapping rewrite called out in §3.3 — a graph-only build failure must not silently inherit the today-only-used-for-"phase-never-spawned" exit 2.

## 5. What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Split `init` the same way | `init` enforces "no existing artifacts" — partial split would let an operator create half an index. Different invariant. UC6 / UC7 cover the half-failed-`init` recovery path through `reprocess --graph-only` / `--vectors-only`; operators previously needed `erase` + `init`, and this PR removes that need without changing the `init` surface. If a real `init --vectors-only` ask surfaces, propose separately. |
| Split `increment` the same way | `increment` is already incremental on the vector side and follows up with a graph rebuild. The use case for "increment vectors but skip graph" is unclear; needs a real ask. |
| Add `--allow-drift` opt-in for partial rebuilds | Inverts principle 3. The operator asked for a partial rebuild on purpose; refusing is paternalistic. |
| Refuse the partial rebuild when drift is detected | We have no cheap drift detector. Computing one (e.g. comparing source-tree hashes between Lance and Kuzu) is a separate, non-trivial design. |
| Add a `--detect-drift` command | Belongs in a separate propose. Useful long-term, out of scope here. |
| Allow `--vectors-only --graph-only` (meaning: both phases) | Two ways to say "both"; the no-flag form already means both. Two ways is one too many. |
| Allow space-separated `--phases=vectors,graph` style | Argparse-native flags are clearer than CSV-encoded lists for a 2-element set. |
| Run the two phases in parallel when both are requested | Belongs in a separate perf propose. Order is intentional (graph builder may read from cocoindex tables in some flows; today it doesn't, but the order documents the safe invariant). |
| Skip the graph builder when `code_graph.kuzu` is up-to-date relative to source mtime | Premature. We don't have a mtime-tracking layer. Build the explicit flags first. |
| Change exit codes for partial runs | Exit code is binary on the requested phase. Drift is stderr-only by principle 6. |
| Move `phases_run` out of the JSON in pretty mode | The pretty renderer already prints `Rebuilt:` / `Skipped:`. JSON keeps `phases_run` for tooling. |
| Rename `reprocess` to something else | Out of scope. The verb name's meaning is unchanged; flags refine which phase. |
| Deprecate the implicit "both phases" default | The default is the most common case (post-refactor, post-pull). Keep it. |

## 6. Migration plan — 1 PR

### PR-REPROCESS-SPLIT-1: `--vectors-only` / `--graph-only` flags on `reprocess`

- **Purpose**: add the two mutually-exclusive flags, factor `_cmd_reprocess` to invoke vectors-only / graph-only / both based on which (if any) was passed.
- **Phase invocation** — both helpers already exist in `java_codebase_rag/pipeline.py`; no extraction is needed:
  - `--vectors-only` calls `run_cocoindex_update(env, full_reprocess=True, quiet=...)` directly — the same helper `_cmd_init` already uses (`cli.py:123`).
  - `--graph-only` calls `run_build_ast_graph(...)` directly — same helper `_cmd_init` uses (`cli.py:135`).
  - Both selective paths skip `import server` entirely (and the implied `sentence_transformers` / `torch` import). The no-flag path keeps `import server` since `server.run_refresh_pipeline` orchestrates both phases today and preserves byte-identical phase orchestration.
- **Exit-code mapping rewrite**: replace `_cmd_reprocess`'s current line 190 (`return 2 if payload.get("exit_code") is None else 1`) with a `phases_run`-aware mapping — exit 2 reserved for "phase never spawned"; exit 1 for any build failure on a requested phase. See §3.3 matrix.
- **Subparser description**: update `cli.py:387` from "Full Lance reprocess plus Kuzu graph rebuild (same as the legacy refresh pipeline)" to reflect the new selective flags.
- **Test summary**: 4 new tests in `tests/test_java_codebase_rag_cli.py` covering:
  - `test_reprocess_vectors_only_skips_graph` — patches the cocoindex + graph helpers; asserts only the cocoindex helper was called; asserts `phases_run == ["vectors"]`; asserts the stderr WARN names "graph".
  - `test_reprocess_graph_only_skips_vectors` — symmetric; asserts only `run_build_ast_graph` was called; `phases_run == ["graph"]`; WARN names "vectors".
  - `test_reprocess_mutually_exclusive_flags_rejected` — argparse rejects `--vectors-only --graph-only`; exit 2.
  - `test_reprocess_graph_only_build_failure_returns_exit_1` — patches `run_build_ast_graph` to return non-zero; asserts CLI exit is **1**, not 2 (regression anchor for the mapping rewrite).
- **No-flag path remains tested by `test_cli_reprocess_builds_kuzu_path` (`tests/test_java_codebase_rag_cli.py:509`)** — this is the regression anchor; it must continue to pass unchanged save for the additive `phases_run` field in its output.

## 7. Decisions taken (no longer open)

1. **Surface shape**: flags on `reprocess`, not subcommands, not sibling verbs.
2. **Flag names**: `--vectors-only` and `--graph-only`. No `--full` (no-flag is full).
3. **Mutual exclusion**: argparse `add_mutually_exclusive_group`. Both flags together is a usage error (exit 2).
4. **Default behaviour**: identical phase order and exit-code semantics when neither flag is passed; JSON gains one additive `phases_run` field (see §3.3 matrix).
5. **Drift policy**: WARN, never refuse. One stderr line naming the unrefreshed store.
6. **Exit code policy**: binary on the requested phase. Drift never affects exit code.
7. **JSON contract**: new optional `phases_run: list[str]` field. All existing fields keep their meaning.
8. **Pretty output**: add `Rebuilt:` / `Skipped:` bullets in the TTY renderer; `Skipped:` includes a hint pointing at the inverse flag.
9. **Phase isolation**: `--vectors-only` does not touch Kuzu; `--graph-only` does not spawn cocoindex.
10. **Phase ordering**: when both run, vectors first, graph second — unchanged from today.
11. **No new subcommands** (`rebuild-graph`, `rebuild-vectors`), no `--phases=...` CSV.
12. **Scope locked to `reprocess`.** `init` and `increment` are not split in this propose.
13. **No drift detector and no `--allow-drift`.** The cost of building either is higher than the cost of an occasional stale partial rebuild.
14. **Flag naming**: `--vectors-only` / `--graph-only` track the operator's mental model ("embedding swap → vectors rebuild"; "route override → graph rebuild"). Alternatives `--no-graph` / `--lance-only` are technically more precise (`--vectors-only` also rebuilds the FTS index and cocoindex state), but operators don't reason about those substores independently. Naming follows mental model, not implementation surface.
15. **`--quiet` does not suppress the drift WARN** (§3.2). `--quiet` is for subprocess verbosity only.

## 8. Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Operator runs `--graph-only` after switching `embedding.model` and gets stale embeddings forever | The drift WARN names "vectors" explicitly; the README §2 path-expansion section already says "Editing `embedding.model` also requires reprocess" — link the WARN message to that note. |
| Operator runs `--vectors-only` after editing `route_overrides` and is confused why their new routes don't appear in `find` / `neighbors` | The WARN names "graph"; pretty output's `Skipped:` line points at `--graph-only`. Documented in CLI guide. |
| JSON consumers that pattern-match on full `reprocess` output break | Highly unlikely (the new field is additive; old fields keep their values for the no-flag path). If anyone reports it, we can ship a compat-only release note. |
| `phases_run` appears confusing in `meta` output | `phases_run` lives in `RefreshIndexOutput`, not in `meta` output. No collision. |
| Argparse mutually-exclusive groups in subparsers don't render nicely in `--help` | Verified informally — argparse handles them fine. Plan-level concern, not propose-level. |
| Selective runs still pull in `sentence_transformers` / `torch` because `_cmd_reprocess` lazy-imports `server` at the top | Both `--vectors-only` and `--graph-only` wire directly to `java_codebase_rag.pipeline` helpers (`run_cocoindex_update`, `run_build_ast_graph`) and skip `import server` entirely. The no-flag path keeps `import server` since `run_refresh_pipeline` orchestrates both phases. Win is symmetric across both selective flags. |
| `_cmd_reprocess`'s current `return 2 if exit_code is None else 1` mapping (`cli.py:190`) silently re-labels a failed `--graph-only` build as exit 2 (today's "phase never spawned" meaning) | Rewrite the mapping in PR-1 to key off the requested phase: exit 2 reserved for "phase never spawned" (missing binary, usage error); exit 1 for a build failure on any requested phase. UC16 + `test_reprocess_graph_only_build_failure_returns_exit_1` (§6) are the regression anchors. |

## Appendix A — Concrete CLI snippets

```bash
# Default — both phases, today's behaviour
$ java-codebase-rag reprocess
{"success": true, "phases_run": ["vectors", "graph"], "exit_code": 0, ...}

# Vectors only — switched embedding.model
$ java-codebase-rag reprocess --vectors-only
java-codebase-rag reprocess: rebuilt vectors only; graph (code_graph.kuzu) was NOT rebuilt and may now reflect a stale source snapshot.
{"success": true, "phases_run": ["vectors"], "exit_code": 0, "graph_exit_code": null, ...}

# Graph only — tweaked YAML route_overrides
$ java-codebase-rag reprocess --graph-only
java-codebase-rag reprocess: rebuilt graph only; vectors (Lance tables under /repo/.java-codebase-rag) were NOT rebuilt and may now reflect a stale source snapshot.
{"success": true, "phases_run": ["graph"], "exit_code": null, "graph_exit_code": 0, ...}

# Mutually exclusive — argparse rejects
$ java-codebase-rag reprocess --vectors-only --graph-only
usage: java-codebase-rag reprocess [-h] [--quiet] [--vectors-only | --graph-only]
java-codebase-rag reprocess: error: argument --graph-only: not allowed with argument --vectors-only
$ echo $?
2
```

## Appendix B — What changed (traceability)

**2026-05-13 — revisions after first review (PR #88).** Status moved `draft → under review`.

- §2 principle 1 + §7 decision 4 + TL;DR: clarified the "byte-identical default" wording — JSON gains an additive `phases_run` field; strict-schema validators may need to allow it. The no-flag *phase semantics* (order, exit codes) are unchanged.
- §3.2: spelled out that `--quiet` does **not** suppress the drift WARN; promoted to decision 15 in §7.
- §3.3: added a per-flag `success` / `exit_code` / `graph_exit_code` / `phases_run` / CLI-exit matrix; flagged the `graph_exit_code = None` / `exit_code = None` ambiguity for old consumers (must read `phases_run` to disambiguate); flagged the CLI exit-code mapping rewrite required for `--graph-only` build failures.
- §4: corrected UC4 to `--graph-only` (`microservice_roots` is consumed by the graph builder only — verified against `java_index_flow_lancedb.py`); reframed UC10 to a `route_overrides` dev-loop (graph builder is not usually the cheaper phase); added UC16 (`--graph-only` build failure → exit 1).
- §5: added init-recovery note — UC6 / UC7 surface `reprocess --graph-only` / `--vectors-only` as the new half-failed-`init` recovery path; previously operators needed `erase` + `init`.
- §6: removed the over-stated "extract `run_vectors_pipeline` or keep inline; plan-level decision" — both helpers already exist in `java_codebase_rag/pipeline.py`. Named the regression-anchor test (`test_cli_reprocess_builds_kuzu_path`). Added 4th test (`test_reprocess_graph_only_build_failure_returns_exit_1`) for the exit-code mapping rewrite. Added explicit deliverables for the exit-code mapping rewrite and the subparser description string at `cli.py:387`.
- §7: added decision 14 on flag naming (operator mental model wins over implementation precision) and decision 15 on `--quiet` ⊥ drift WARN.
- §8: strengthened the lazy-import row (both selective paths skip `import server`); added a row for the exit-code mapping rewrite with UC16 + new test as anchors.
