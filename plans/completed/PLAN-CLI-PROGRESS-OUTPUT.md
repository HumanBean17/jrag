# Plan: CLI progress output (Phase 1 — stream + heartbeats)

Status: **completed**. This plan implements
[`propose/completed/CLI-PROGRESS-OUTPUT-PROPOSE.md`](../../propose/completed/CLI-PROGRESS-OUTPUT-PROPOSE.md).

Depends on: **none** (orthogonal to graph schema / ontology). No `ontology_version` bump and no re-index requirement.

## Goal

- **Stop buffering** lifecycle subprocess output until exit: relay each child’s **stdout and stderr** to the operator’s **stderr** as bytes arrive (verbatim), while still accumulating the same tail windows for structured results (`RefreshIndexOutput`, CLI failure payloads).
- **Bracket opaque phases** with honest stderr lines: cocoindex wrap (`[lance] …`), pipeline header/footer (`java_codebase_rag/cli.py`), pass-start lines and **5 s** heartbeats in `build_ast_graph.py` (verbose path only).
- **Preserve contracts**: machine-readable **`java-codebase-rag` stdout** for `init` / `increment` / `reprocess` / `erase` stays **byte-for-byte identical** to today; under `--quiet`, **stderr matches a per-subcommand baseline from today** (no **new** bytes from streaming relay, `[lance]` wrap, header/footer, pass starts, or heartbeats). Pre-existing stderr stays unchanged — notably `increment --quiet` always prints the multi-line Kuzu staleness warning today and continues to; `meta` / `tables` / `diagnose-ignore` / `analyze-pr` unchanged.

## Principles (do not relitigate in review)

- **Stream first; no pretty UI in this round** — no `rich` / `tqdm` / `click`, no ANSI, no TTY-only rendering (deferred to a future `CLI-PRETTY-OUTPUT` propose).
- **Stderr = human channel; CLI stdout = agent/CI contract** — relayed subprocess bytes go to **stderr**, not stdout.
- **No parsing or reformatting** of cocoindex or graph-builder lines; wrap lines are additive CLI-owned prefixes only.
- **Summary line grep parity** — existing `[passN] …` summary strings in `build_ast_graph.py` stay **verbatim**; only **new** start/heartbeat lines are added.
- **Heartbeat cadence fixed at 5 s** (integer seconds in messages); not configurable in this rollout.
- **Quiet is sacred** — `quiet=True` / `--quiet` must keep capture-only subprocess behaviour (no live relay) **and** must not add any **new** stderr markers from this work. **Parity = stderr byte-for-byte equal to today's baseline per subcommand**, not `stderr == ""` for every command (`increment --quiet` already emits the staleness warning block).
- **Five improvements, two implementation PRs after propose** — align with propose §6: structural streaming PR, then cosmetic PR (+ docs). Propose’s “PR-PROG-1 = propose merge” is documentation land; once the propose is on the target branch, implementation starts at propose’s PR-PROG-2.

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Propose merge / scope lock (if not already on main) | no | `propose/completed/CLI-PROGRESS-OUTPUT-PROPOSE.md` | none | none |
| PR-2 | Live stream stdout+stderr; cocoindex `[lance]` wrap; full buffers for tails; quiet parity | no | `server.py`, `java_codebase_rag/pipeline.py`, `java_codebase_rag/cli.py`, new test module | unit + integration (stdout invariant, quiet) | PR-1 if propose already merged |
| PR-3 | Pass-start lines, 5 s heartbeats (`build_ast_graph.py` + `write`), pipeline header/footer, README + `docs/JAVA-CODEBASE-RAG-CLI.md` + `docs/AGENT-GUIDE.md` + `--help` one-liner | no | `build_ast_graph.py`, `java_codebase_rag/cli.py`, `README.md`, CLI docs, agent guide, tests | heartbeat ordering, header/footer, quiet extension, stdout invariant regression | PR-2 |

Landing order: **PR-1 (optional) → PR-2 → PR-3**.

## Ground truth vs propose (implementation must not miss this)

| Topic | Decision |
| --- | --- |
| Where `reprocess` buffers today | `server.py::run_refresh_pipeline` uses `asyncio.create_subprocess_exec` with `PIPE` + `communicate()`. |
| Where `init` / `increment` buffer today | `java_codebase_rag/pipeline.py` uses `subprocess.run(..., capture_output=True)` for cocoindex and for `build_ast_graph.py`. **PR-2 must stream both paths**, not only `run_refresh_pipeline`, or `init` remains silent until exit. |
| `erase` | Uses `pipeline.run_cocoindex_drop` and in-process deletes; propose’s header/footer still apply in PR-3. Optional: stream `cocoindex drop` stderr in non-quiet later; not required for propose UC6 if drop stays fast. |
| MCP stdio rule | Tool handlers must not write to stdout; **`run_refresh_pipeline` is CLI-only today** — keep relay on **stderr** only. |

---

# PR-1 — Propose merge (optional)

## File-by-file changes

### 1. `propose/completed/CLI-PROGRESS-OUTPUT-PROPOSE.md`

- Land or refresh status so the propose is the reviewed anchor for Phase 1 scope and Phase 2 deferrals.

## Tests for PR-1

- None (documentation only).

## Definition of done (PR-1)

- Propose is merged to the integration branch with §7 decisions intact.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Confirm propose merged | `propose/completed/CLI-PROGRESS-OUTPUT-PROPOSE.md` | PR-2 branch can rebase on it |

---

# PR-2 — Stream subprocess I/O + cocoindex wrap

## File-by-file changes

### 1. `server.py` (`run_refresh_pipeline`)

- Replace `communicate()`-style buffering for cocoindex and `build_ast_graph.py` with **concurrent async readers** on both `stdout` and `stderr`.
- While `quiet=False`: relay each chunk **verbatim** to **`sys.stderr`** (preserve bytes/decoding strategy; document `errors="replace"` if kept).
- Always append to in-memory strings for **`RefreshIndexOutput`** (`stdout`/`stderr`/`graph_stdout`/`graph_stderr`) so tail clipping (`clip` / last N chars) matches current field semantics.
- Emit propose Appendix A lines around cocoindex only when `quiet=False`:
  - `[lance] running cocoindex update (project_root=<root>)`
  - `[lance] cocoindex update finished in <X.XX>s (exit=<code>)`
- When `quiet=True`: keep **capture-only** behaviour (no relay), identical tail attachment semantics.

### 2. `java_codebase_rag/pipeline.py`

- Refactor `run_cocoindex_update` and `run_build_ast_graph` (and optionally `run_cocoindex_drop`) so non-quiet paths **stream** child stdout+stderr to the parent stderr while still returning `CompletedProcess`-compatible strings (full or tailed — match whatever the CLI expects today for failure messages).
- Quiet paths: retain `capture_output=True` (or equivalent) with no relay.

### 3. `java_codebase_rag/cli.py`

- `_cmd_init` / `_cmd_increment`: call streaming-aware pipeline helpers; emit the same `[lance]` bracket lines around cocoindex as `run_refresh_pipeline` (shared small helper in `pipeline.py` or `cli.py` to avoid drift).
- Do **not** change stdout JSON / pprint payloads or exit-code mapping.

## Tests for PR-2

Prefer a dedicated module `tests/test_cli_progress_stdout_invariant.py` (name from propose §6) grouping stdout baseline checks.

1. `test_stream_relay_arrives_before_wait` — asyncio (or threaded) fake child: bytes written to child stdout/stderr appear on a **sink** before process exit, proving no end-of-process batching in non-quiet mode.
2. `test_refresh_pipeline_quiet_stderr_baseline` — `run_refresh_pipeline(quiet=True)`: stderr has **no new** progress markers from this work; compare to baseline or assert relay/wrap lines absent (subprocess output captured only, as today).
3. `test_cli_lifecycle_stdout_invariant_init` — `java-codebase-rag init --quiet` (tiny fixture, temp index dir): captured **stdout** matches a **checked-in baseline** string (propose §3.4).
4. `test_cli_lifecycle_stdout_invariant_reprocess` — same for `reprocess --quiet` when CI can run the pipeline; if full cocoindex is unavoidable, gate behind `JAVA_CODEBASE_RAG_RUN_HEAVY` **only as a last resort** — prefer stubbed subprocesses or payload builders so default `pytest tests` stays ungated.

## Definition of done (PR-2)

- Interactive `init`, `increment`, and `reprocess` show live subprocess output on stderr (when not `--quiet`).
- `RefreshIndexOutput` fields remain populated for success and failure cases with comparable tail limits.
- Ruff + pytest green per `AGENTS.md`.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Extract or implement async byte relay helper | `server.py` (+ small `_cli_progress.py` only if it reduces duplication) | Both cocoindex and graph subprocesses covered |
| 2 | Mirror streaming for sync CLI path | `pipeline.py`, `cli.py` | `init`/`increment` no longer buffer until exit |
| 3 | Add `[lance]` wrap in both server and CLI cocoindex call sites | `server.py`, `cli.py` | Wording matches propose Appendix A |
| 4 | Add tests | `tests/test_cli_progress_stdout_invariant.py` (+ streaming unit module as needed) | All PR-2 tests pass |

---

# PR-3 — Pass starts, heartbeats, pipeline header/footer, docs

## File-by-file changes

### 1. `build_ast_graph.py`

- For each pass **1–6** and the **write** block (verbose mode): print **start** line from propose Appendix B **before** work; keep existing **summary** lines unchanged.
- Add **heartbeat** context manager (or task) emitting `[passN] running … <int>s elapsed` every **5 s** on stderr, `flush=True`, guarded by a **small lock** so heartbeat lines do not interleave mid-line with other prints; cancel on pass exit **including exceptions** (propose §8).
- Suppress start lines + heartbeats when not verbose (`--quiet` path from CLI already drops `--verbose`).

### 2. `java_codebase_rag/cli.py`

- Wrap `init` / `increment` / `reprocess` / `erase` with **header** and **footer** (propose Appendix A) on stderr when not `--quiet`.
- Timer uses monotonic clock; durations `X.XX` two decimal places; middle-dot `·` separators; `exit=<code>` on footer.
- `refresh` alias path: deprecation line remains; header/footer bracket **`reprocess`** semantics (same subcommand label as executed handler).

### 3. `README.md` (CLI section)

- One sentence: lifecycle commands stream subprocess progress to **stderr** (including relayed child stdout); `--quiet` suppresses it; stdout remains the machine contract.

### 4. `docs/JAVA-CODEBASE-RAG-CLI.md`

- Same operator-facing note under output / lifecycle area.

### 5. `docs/AGENT-GUIDE.md`

- Same note for agent operators driving the CLI.

### 6. `java_codebase_rag/cli.py` (`build_parser` description)

- One sentence in top-level `--help` description string (propose UC16).

## Tests for PR-3

Use `tests/test_cli_quiet_parity.py` for quiet regression (propose §8 / §6).

1. `test_pass_heartbeat_fires_when_pass_slowed` — inject a delay stub or env-controlled slow fixture so a pass exceeds 5 s; assert at least one heartbeat line **before** summary.
2. `test_pass_start_before_pass_body` — start line appears before first pass-specific verbose output.
3. `test_pipeline_header_footer_present` — non-quiet lifecycle command includes header regex and footer regex on stderr.
4. `test_cli_quiet_stderr_baseline_per_subcommand` — for `init` / `increment` / `reprocess` / `erase --yes` with `--quiet`, captured **stderr** equals a **checked-in per-subcommand baseline** from current behaviour (or assert absence of new markers: `[lance]`, `java-codebase-rag … ·`, `[passN] starting`, `[passN] running …`). **Expect non-empty baseline for `increment --quiet`** (staleness warning).
5. Re-run / extend `test_cli_lifecycle_stdout_invariant_*` from PR-2 — stdout baselines still match.

## Definition of done (PR-3)

- Output spec in propose Appendix A satisfied for normal `init` / `reprocess` runs (modulo real cocoindex volume).
- Documentation and `--help` mention stderr streaming + `--quiet`.
- Full `tests` suite + ruff per repo workflow.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Implement `heartbeat()` context manager | `build_ast_graph.py` | Exception-safe cancellation + lock |
| 2 | Insert Appendix B start strings | `build_ast_graph.py` | Grep parity on old summaries |
| 3 | Header/footer helpers | `cli.py` | All four lifecycle verbs wrapped |
| 4 | Docs + help string | `README.md`, `docs/*.md`, `cli.py` | Single consistent sentence |
| 5 | Tests | `tests/…` | PR-3 tests + PR-2 invariants green |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Line interleaving from concurrent stderr writes | Medium | Module-level `threading.Lock` around **single** `print(..., flush=True)` calls (propose §8). |
| 2 | Heartbeat thread leaks on exception | Medium | Context manager `__exit__` always cancels background worker; unit test exception path. |
| 3 | Quiet tests assert `stderr == ""` for `increment --quiet` and fail | Medium | Lock rule in propose §3.3 + plan: **baseline parity**; record `increment` quiet stderr fixture including staleness block. |
| 4 | Accidental timestamp or progress on stdout | High | Baseline byte comparison tests; code review: only `_emit` / `print` to stdout for payloads. |
| 5 | `init` path forgotten | High | Explicit `pipeline.py` + `cli.py` scope in PR-2; grep for `capture_output=True` after PR-2. |
| 6 | UTF-8 decode errors on relay | Low | Keep `errors="replace"` consistent with today’s decode of captured bytes. |

# Out of scope

- Pretty rendering, colors, progress bars, `rich` / `tqdm` / `click`.
- Changing summary line text in `build_ast_graph.py`.
- `meta`, `tables`, `diagnose-ignore`, `analyze-pr` output or timing.
- Configurable heartbeat interval or `--format=json` for human progress.
- Parsing or summarizing cocoindex output beyond the two `[lance]` lines.
- i18n / translated stderr.

# Whole-plan done definition

1. Long `init` / `reprocess` runs emit visible stderr at most ~5 s apart during graph passes (verbose) and stream cocoindex + builder child output live when not `--quiet`.
2. `--quiet` lifecycle runs: stdout payloads match checked baselines; **stderr matches per-subcommand baselines from today** (no new markers from this work; `increment --quiet` baseline includes the existing staleness warning).
3. Documentation and `--help` describe stderr streaming and `--quiet` suppression.
4. `propose/completed/CLI-PROGRESS-OUTPUT-PROPOSE.md` status updated to **completed** when the feature set is merged; this plan moved to `plans/completed/` after the final PR lands.

# Tracking

- `PR-1` (propose): _done_
- `PR-2` (stream + wrap): _done_
- `PR-3` (heartbeats + docs): _done_

Optional follow-up: add `plans/AGENT-PROMPTS-CLI-PROGRESS-OUTPUT.md` using `plans/completed/AGENT-PROMPTS-TIER1B.md` as the structural template for per-PR Cursor handoffs.
