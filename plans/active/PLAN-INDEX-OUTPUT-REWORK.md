# Plan: unified progress-bearing index-build output

Status: **active (planning)**. This plan implements
[`propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md`](../../propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md).
This file is plan-only and does not implement code.

Depends on: a **throwaway spike** that validates CocoIndex relays flow-function
stderr to the parent (see Spike section). PR-1 does not start until the spike passes.

## Goal

- One shared, `rich`-based renderer draws the index-build output (header + per-phase
  progress list + footer) on stderr for **all five** lifecycle commands (`init`,
  `increment`, `install`, `reprocess`, `update`), replacing the per-command,
  sometimes-silent output of today.
- A single `JCIRAG_PROGRESS` structured-line protocol carries real progress across
  the subprocess boundary for both the vectors phase (CocoIndex) and the graph phase
  (`build_ast_graph.py`), so both phases render a real bar (determinate where the
  denominator is knowable; indeterminate otherwise), not just an elapsed spinner.
- `install`/`update` stop being silent (`update` drops `quiet=True`) and their
  indexing progress moves off stdout onto the stderr renderer, while each command's
  stdout contract is preserved.

## Principles (do not relitigate in review)

- **stdout = machine payload; stderr = human progress.** No command writes indexing
  progress to stdout. `init`/`increment`/`reprocess` keep their JSON/pprint stdout;
  `install`/`update` keep their human-readable wizard stdout.
- **CocoIndex stays a subprocess.** Do not switch to the in-process `app.update()`/
  `watch()` API — that re-introduces the native-thread shutdown crash the subprocess
  isolation exists to avoid (`_console_script_main` → `os._exit`).
- **One renderer, one code path.** `install`/`update` are un-silenced to engage the
  same renderer the operator commands use — they are **not** wrapped in
  `_run_with_pipeline_progress` (that would put wizard prompts under the
  header/footer framing).
- **Determinate where the denominator is knowable; indeterminate where it isn't.**
  Graph pass 1 is exactly determinate (count-first, single-layer ignore). Vectors is
  *approximately* determinate on full reprocess (bar clamps to 100% on completion)
  and indeterminate on incremental catch-up (`memo=True`).
- **Single stderr writer while the `rich` `Live` region is up.** Non-progress lines
  route through `console.print(...)`; raw `sys.stderr.buffer.write` relay happens
  only in `--verbose` (no `Live` region).
- **A task is `running` only after its subprocess actually spawns.** The 126/127
  pre-spawn stubs emit `status=failed` and never mark a task `running`.
- **Three verbosity tiers preserved.** `--quiet` = no progress stderr; default =
  `rich` display; `--verbose` = raw subprocess relay (no `Live` region).
- **No new CLI flags.** Existing `--quiet`/`--verbose` flags are wired through on
  `update` (both ignored today) and `install` (`--verbose` ignored today).

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| Spike | Validate CocoIndex relays flow-function stderr; size pre-walk divergence | none | none (throwaway, no merge) | none (go/no-go note) | — |
| PR-1 | `rich` dep + `progress.py` (parser + renderer + non-TTY fallback + relay invariant); unit tests | none | parser vs existing `_LineFilter`/relay; rich `Live` single-writer; non-TTY fallback | progress unit (light) | Spike |
| PR-2 | Graph-phase `JCIRAG_PROGRESS` emission (`build_ast_graph.py` count-first pass 1 + passes 2–6); wire renderer into operator commands' graph phase | none | pass-1 count-first cost; emission gating; `_run_with_pipeline_progress` ↔ renderer integration | graph progress + CLI (light) | PR-1 |
| PR-3 | Vectors-phase `JCIRAG_PROGRESS` emission (`process_*_file` + approximate pre-walk); wire renderer into vectors phase incl. both `Optimize` call sites; retire `emit_vectors_*` | none | two-layer filtering / approximate denominator; `memo=True` indeterminacy; two optimize call sites; flow-stderr assumption | flow progress (heavy-gated) + divergence | PR-1, PR-2 |
| PR-4 | Un-silence `install`/`update` subprocess calls; wire `--quiet`/`--verbose` through `update`/`install`; docs | none | wizard stdout contract preserved; verbosity wiring; renderer wraps indexing sub-step only | installer + CLI (light) | PR-2, PR-3 |

Landing order: **Spike (gate) → PR-1 → PR-2 → PR-3 → PR-4**. Do not start PR-1 until
the spike passes; do not start PR-N+1 until PR-N is merged to `master`.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Renderer library | `rich` (`rich>=13.7,<14`); parent-process only. |
| New module | `java_codebase_rag/progress.py` holds `ProgressEvent`, `parse_progress_line`, `IndexProgressRenderer`, `ProgressRelay`. |
| Protocol line | `JCIRAG_PROGRESS  kind=<vectors\|graph\|optimize>  [phase=…] [pass=N/6] [done=N] [total=N] [status=running\|done\|failed] [elapsed_s=…]` |
| Drain integration | `pipeline._popen_capturing_stderr` and `cli_progress.accumulate_and_relay_subprocess_streams` gain an optional `on_progress` callback and route complete lines through a `ProgressRelay` (parse-first; progress lines suppressed from relay). |
| Emission gating | `build_ast_graph.py` emits `JCIRAG_PROGRESS` under its existing `--verbose` path (the parent already passes `--verbose` for default+verbose, only suppresses it for `--quiet`). The flow emits `JCIRAG_PROGRESS` unconditionally to its stderr; parent mode controls handling. No new CLI flags on `build_ast_graph.py`. |
| Single stderr writer | While the `rich` `Live` region is active, `ProgressRelay` prints non-progress lines via `console.print(...)`; raw `buffer.write` relay runs only when there is no `Live` region (`--verbose`). |
| Graph pass-1 total | Count-first: one filtered `os.walk` (no parse) sets the exact total, then the parse loop ticks per file. Single-layer ignore → exact. |
| Vectors full-reprocess total | Approximate pre-walk (matcher includes + layered-ignore); bar clamps to 100% on `status=done`. Authoritative-count-from-flow is the escalation path if the spike's divergence test shows a large gap. |
| Vectors incremental total | `total=None` (indeterminate pulsing bar) + "files touched: N" counter (`memo=True` only calls the function for changed files). |
| `Optimize` phase | Surfaced as its own task; auto-collapses to a vectors sub-state when it completes under ~1 s (Open Q4). Both call sites emit `kind=optimize`. |
| `Spinner` | Retired (only caller is the vectors phase, replaced by the renderer). |
| `install`/`update` wiring | Un-silence the `run_cocoindex_update`/`run_build_ast_graph` calls (drop `quiet=True`, pass progress context); do not wrap the wizards. `update` gets `--quiet`/`--verbose` wired through `_cmd_update`/`run_update`; `install` gets `--verbose` wired. |

---

# Spike — validate CocoIndex flow-function stderr relay (gate, no PR)

**Branch:** throwaway (e.g. `spike/flow-stderr`), not merged. **Not** a deliverable on
`master`.

## Objective

Confirm the load-bearing assumption: a line written to `sys.stderr` from inside
`process_java_file` (`java_index_flow_lancedb.py`) reaches the parent process that
spawned `cocoindex update`. Also size the vectors pre-walk divergence.

## Steps

### 1. `java_index_flow_lancedb.py` (throwaway edit, not merged)
- At the top of `process_java_file`, before the ignore check, emit one line:
  `print("JCIRAG_PROGRESS kind=vectors phase=java done=0 total=0 status=running", file=sys.stderr, flush=True)`.

### 2. Drive + capture
- Run `cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance --full-reprocess`
  against `tests/bank-chat-system` with stdout/stderr captured to pipes (mirror
  `pipeline._popen_capturing_stderr`'s capture).
- Inspect captured stderr for the `JCIRAG_PROGRESS` line.

### 3. Pre-walk divergence
- Pre-walk `tests/bank-chat-system` with the matcher includes + `LayeredIgnore`
  (`cocoindex_excluded_patterns()` + `is_ignored`) and record the count.
- Record the actual `done` at completion.
- Compute the gap (ignored/empty/undecodable files).

## Done when

- **Go/no-go written** (a one-paragraph note on the throwaway branch): either
  "stderr relays — proceed with PR-1" with the divergence number, or
  "stderr suppressed — halt, re-propose transport". The gate is binary.

## Result — **GO** (2026-06-14)

Spike passed. A `print("JCIRAG_PROGRESS …", file=sys.stderr, flush=True)` at the top
of `process_java_file`, run via `cocoindex update … --full-reprocess` on
`tests/bank-chat-system`, produced **130** `JCIRAG_PROGRESS` lines in captured stderr
(cocoindex exit 0) — flow-function stderr reaches the parent unmodified, no
suppression/buffering. Pre-walk divergence: **0** (130 non-ignored `.java` files ==
130 processed). PR-1 is unblocked.

---

# PR-1 — `rich` dep + `progress.py` (parser, renderer, non-TTY fallback, relay)

**Branch:** `feat/index-progress-protocol` off `master` **after the spike passes**.
No command wiring, no flow/builder emission in this PR — pure library + unit tests.

## File-by-file changes

### 1. `pyproject.toml`
- Add `rich>=13.7,<14` to `dependencies`.

### 2. `java_codebase_rag/progress.py` (new)
- `ProgressEvent` dataclass: `kind` (`Literal["vectors","graph","optimize"]`),
  `phase: str | None`, `pass_: str | None` (e.g. `"3/6"`), `done: int | None`,
  `total: int | None`, `status: Literal["running","done","failed"]`, `elapsed_s: float | None`.
- `parse_progress_line(line: bytes) -> ProgressEvent | None` — returns `None` for any
  line not starting with the `JCIRAG_PROGRESS` prefix; parses `key=value` tokens;
  tolerates extra spaces; never raises.
- `IndexProgressRenderer`:
  - `__init__(self, phases: list[str], *, console: Console | None = None)` — builds a
    `rich.progress.Progress` (TTY) or prepares the concise-line fallback (non-TTY);
    one task per phase, all `total=None` until a `done`/`total` event arrives;
  - `start()` / `stop()` — enter/exit the `Live` region (TTY) or no-op (non-TTY);
  - `apply(self, ev: ProgressEvent)` — update the matching task: on `total` set the
    denominator; on `done` advance; on `status=done` **clamp completed to total** and
    mark the task finished; on `status=failed` mark the task failed (red `✗`);
  - a task is only marked visible/`running` after the first event for its `kind`
    arrives (so a never-spawned phase stays pending, never `running`);
  - the non-TTY fallback prints concise lines on `apply` at most every ~5 s per phase
    plus on every terminal (`done`/`failed`) event.
- `ProgressRelay`:
  - wraps the existing line-buffering used by `_LineFilter` / `_AsyncLineFilter`;
  - `feed(self, chunk: bytes) -> None` — reassembles complete lines; for each, run
    `parse_progress_line` first; if it returns an event, call `renderer.apply(ev)` and
    **suppress** the line from relay; otherwise hand the line to the configured
    sink: `console.print(...)` while the `Live` region is up, or raw
    `sys.stderr.buffer.write` when relaying verbatim (`--verbose`, no `Live` region);
    non-progress lines still pass through the `_NOISE_CONTAINS` noise matcher before
    the sink;
  - `flush()` — drain the partial buffer.

### 3. `tests/test_progress.py` (new)
- Pure unit tests against `progress.py`; no subprocess, no cocoindex, no torch —
  fully light.

## Tests for PR-1

1. `test_parse_progress_line_vectors_running`
2. `test_parse_progress_line_graph_pass`
3. `test_parse_progress_line_optimize_running`
4. `test_parse_progress_line_done_with_elapsed`
5. `test_parse_progress_line_non_progress_returns_none`
6. `test_parse_progress_line_malformed_returns_none`
7. `test_progress_relay_parses_split_chunk_once` (one logical line split across two
   `feed()` calls → exactly one `apply` call, no relay of the raw line)
8. `test_progress_relay_relays_non_progress_line` (a non-progress line reaches the sink)
9. `test_renderer_task_pending_until_first_event` (no event ⇒ task not `running`)
10. `test_renderer_clamps_completed_to_total_on_done`
11. `test_renderer_indeterminate_total_none` (`total=None` ⇒ pulsing bar, no `%`)
12. `test_renderer_failed_marks_task_red`
13. `test_non_tty_fallback_emits_concise_lines` (renderer constructed with a non-TTY
    console ⇒ prints interval lines + a terminal line)

## Definition of done (PR-1)

- [ ] `rich>=13.7,<14` in `pyproject.toml`; `.venv/bin/pip install -e .` succeeds.
- [ ] `progress.py` exists with the four symbols above; no production caller yet.
- [ ] All 13 `tests/test_progress.py` tests pass.
- [ ] `.venv/bin/ruff check .` clean; `.venv/bin/python -m pytest tests -v` green
      (no new heavy gating introduced — all new tests are light).
- [ ] No `JCIRAG_PROGRESS` emission added to production files yet.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `rich` dep | `pyproject.toml` | `pip install -e .` pulls rich |
| 2 | `ProgressEvent` + `parse_progress_line` | `progress.py` | parser tests 1–6 pass |
| 3 | `ProgressRelay` (split-chunk, parse-first, single-writer sink) | `progress.py` | relay tests 7–8 pass |
| 4 | `IndexProgressRenderer` (TTY `rich` + non-TTY fallback + clamp) | `progress.py` | renderer tests 9–13 pass |
| 5 | Full suite green | — | `ruff` + `pytest tests -v` pass |

---

# PR-2 — graph-phase progress (operator commands)

**Branch:** `feat/index-graph-progress` off `master` **after PR-1 merged**.

## File-by-file changes

### 1. `build_ast_graph.py`
- Add a `_emit_progress(parts: dict[str,str])` helper that writes one
  `JCIRAG_PROGRESS kind=graph …` line to stderr, flushed, gated by the existing
  verbose flag (so the parent's `--verbose`-on-non-quiet path emits it; `--quiet`
  suppresses the whole subprocess relay anyway).
- `pass1_parse`: add a cheap count-first step — one filtered `os.walk` over
  `iter_java_source_files(root, ignore=ignore)` semantics (no parsing) to set the
  total, then emit `pass=1 total=N` once and a `done=k` tick per parsed file
  (throttled every ~25 files + on pass completion).
- `pass2_edges` … `pass6_match_edges`: emit `pass=N/6 status=running` on entry and
  `pass=N/6 status=done elapsed_s=…` on exit.
- Keep the existing `[graph] pass N` heartbeat lines for `--verbose` raw relay; the
  new structured lines are additive.

### 2. `java_codebase_rag/pipeline.py`
- `_popen_capturing_stderr`: accept an optional `on_progress: Callable[[ProgressEvent], None] | None`.
  Replace the inline `_LineFilter` drain with a `ProgressRelay` that, per complete
  line, parses first; progress events → `on_progress`; non-progress → existing
  noise/relay path (raw `buffer.write` here, since this helper is the verbatim-relay
  path used in default+verbose; the `console.print` routing is used by the renderer
  context in step 4).
- `run_build_ast_graph` / `run_incremental_graph`: thread `on_progress` through from
  the caller; when `--verbose` is the mode, do not attach a renderer (raw relay) —
  pass `on_progress=None`.

### 3. `java_codebase_rag/cli_progress.py`
- `accumulate_and_relay_subprocess_streams` (async, used by `server.run_refresh_pipeline`):
  same `ProgressRelay` + `on_progress` wiring as the sync helper.

### 4. `java_codebase_rag/cli.py`
- Introduce a renderer context around `_run_with_pipeline_progress`'s `work()` (TTY
  only; `--quiet` skips it, `--verbose` skips it). For this PR it owns the **graph**
  task: create the phase list from the command's phase set, mark the graph task
  `running` only once `run_build_ast_graph`/`run_incremental_graph` actually spawns,
  feed `ProgressRelay` events into the renderer, and route non-progress relay lines
  through `console.print` while the `Live` region is up.
- `_cmd_init`, `_cmd_increment`, `_cmd_reprocess`: pass the graph-phase `on_progress`
  callback through the pipeline helpers. The vectors task remains pending in this PR
  (PR-3 fills it).

### 5. `tests/test_ast_graph_build.py`
- Add graph-progress tests (run the builder against `tests/bank-chat-system`; assert
  on stderr lines, not ANSI).

### 6. `tests/test_java_codebase_rag_cli.py`
- Add CLI-level assertions that graph-phase progress reaches stderr in default mode
  and is absent in `--quiet`, by patching the pipeline helpers to a fixture subprocess
  that emits known `JCIRAG_PROGRESS` lines.

## Tests for PR-2

1. `test_build_ast_graph_pass1_emits_per_file_progress` (count-first: a `total=` line
   precedes the first `done=`; ticks advance)
2. `test_build_ast_graph_pass1_total_is_exact_filtered_count` (total == count of
   non-ignored `.java` files in the fixture)
3. `test_build_ast_graph_passes_2_to_6_emit_step_progress` (six `pass=N/6` lines)
4. `test_build_ast_graph_quiet_emits_no_progress` (`--quiet` ⇒ no `JCIRAG_PROGRESS`)
5. `test_cli_init_default_mode_graph_phase_progress_on_stderr` (patched helper emits
   a graph line; assert it is parsed and not raw-relayed in default mode)
6. `test_cli_increment_graph_phase_progress` (symmetric)
7. `test_cli_graph_progress_absent_when_quiet`

(Tests 5–7 patch the pipeline helpers so they do **not** require cocoindex/torch and
run in the default light suite.)

## Definition of done (PR-2)

- [ ] `build_ast_graph.py` emits `kind=graph` progress (count-first pass 1 exact total;
      passes 2–6 step) under the non-quiet path.
- [ ] `_popen_capturing_stderr` and the async drain route progress events to a caller
      callback and suppress them from raw relay.
- [ ] `init`/`increment`/`reprocess` render the graph task (determinate) in default
      TTY mode; `--quiet` shows nothing; `--verbose` raw-relays.
- [ ] All 7 PR-2 tests pass; `ruff` + full `pytest tests -v` green.
- [ ] `install`/`update` unchanged in this PR (still `quiet=True`).

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | `_emit_progress` helper + pass 2–6 step lines | `build_ast_graph.py` | tests 3–4 pass |
| 2 | Count-first pass 1 + per-file ticks | `build_ast_graph.py` | tests 1–2 pass |
| 3 | `on_progress` plumbing in sync + async drains | `pipeline.py`, `cli_progress.py` | relay routes events, suppresses raw |
| 4 | Renderer context for graph task in operator commands | `cli.py` | tests 5–7 pass |
| 5 | Full suite green | — | `ruff` + `pytest tests -v` pass |

---

# PR-3 — vectors-phase progress (operator commands) + retire `emit_vectors_*`

**Branch:** `feat/index-vectors-progress` off `master` **after PR-2 merged**.

## File-by-file changes

### 1. `java_index_flow_lancedb.py`
- Add a small `_emit_vectors_progress(kind, done, total, status, elapsed_s=None)`
  helper writing `JCIRAG_PROGRESS kind=vectors …` to stderr (flushed).
- In `app_main`, after mounting, emit a one-shot approximate total: walk the three
  matchers (`**/*.java`, `…/migration/*.sql`, `application*.yml`) through
  `LayeredIgnore` (`cocoindex_excluded_patterns()` + `is_ignored`) and emit
  `kind=vectors total=N status=running` (approximate — ignored/empty/undecodable files
  overstate it; the parent clamps on completion).
- In each `process_*_file`, increment a module-level atomic counter and emit a
  `done=k` tick every ~25 files and on the final file (`status=done elapsed_s=…`).
  On incremental catch-up (`memo=True` cache hit ⇒ function not called) no total is
  known — the parent renders indeterminate from the absence of a `total` event.

### 2. `java_codebase_rag/pipeline.py`
- `_run_cocoindex_update_impl`: the `on_progress` plumbing from PR-2 now also carries
  `kind=vectors` events. Drop the `Spinner` and the `emit_vectors_start`/`_finish`
  calls; the renderer owns the vectors task instead. A vectors task is marked
  `running` only after the `cocoindex` `Popen` succeeds (not on the 127 stub).

### 3. `server.py`
- `run_refresh_pipeline`: route the async-drain vectors events into the same renderer;
  drop `emit_vectors_start`/`emit_vectors_finish` here too. The serialized optimize
  block (`server.py:359-372`) emits `kind=optimize status=running` / `status=done`.

### 4. `java_codebase_rag/lance_optimize.py`
- `optimize_lance_tables`: emit `JCIRAG_PROGRESS kind=optimize status=running` on
  entry and `status=done elapsed_s=…` on exit — this is the **second** optimize call
  site (`_maybe_run_serialized_optimize` in `pipeline.py:129` calls it), so both
  reprocess-default and init/increment paths surface the phase consistently.

### 5. `java_codebase_rag/cli_progress.py`
- Remove `emit_vectors_start` / `emit_vectors_finish` (no remaining callers after
  steps 2–3). Keep `_AsyncLineFilter` logic folded into `ProgressRelay` (PR-2).

### 6. `java_codebase_rag/cli_format.py`
- Remove the `Spinner` class (only caller was the vectors phase, now retired).

### 7. `java_codebase_rag/cli.py`
- Extend the renderer context from PR-2 to own the **vectors** and **optimize** tasks
  for the operator commands: vectors task `running` after `cocoindex` spawns;
  optimize task on the optimize events; phase list order
  `Vectors → Optimize → Graph`.

### 8. `tests/test_java_codebase_rag_cli.py` and a new `tests/test_vectors_progress.py`
- Heavy (cocoindex) flow tests gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1`; the
  parser/divergence/clamp tests stay light via patched helpers.

## Tests for PR-3

1. `test_flow_emits_vectors_progress_per_file` (**heavy-gated**) — run
   `cocoindex update` on the fixture, assert `JCIRAG_PROGRESS kind=vectors` lines
   appear in captured stderr (the spike, promoted to a regression test).
2. `test_vectors_progress_clamps_on_completion` (light — feed a synthetic event
   stream through the renderer: `total=100`, `done=80`, `status=done` ⇒ completed
   clamps to 100).
3. `test_vectors_progress_approximate_total_overstates_then_clamps` (light — feed
   `total=100` then `done=95 status=done`; assert clamp, no 95% stall).
4. `test_vectors_incremental_renders_indeterminate` (light — no `total` event ⇒
   pulsing bar / `MofN` shows `?`).
5. `test_pre_walk_total_divergence_bounded` (**heavy-gated**) — on the fixture,
   pre-walk total vs. actual `done` differ only by the ignored/empty count.
6. `test_cli_init_vectors_phase_progress_on_stderr` (light — patched cocoindex helper
   emits a vectors line; parsed, not raw-relayed).
7. `test_cli_reprocess_optimize_phase_progress` (light — patched optimize emits
   `kind=optimize`; phase renders).
8. `test_spinner_removed_and_emit_vectors_helpers_removed` (light — import guard:
   `Spinner` and `emit_vectors_start`/`_finish` no longer exist).

## Definition of done (PR-3)

- [ ] `process_*_file` emit `kind=vectors` progress; approximate total emitted from
      `app_main`; bar clamps to 100% on completion.
- [ ] Both optimize call sites (`run_refresh_pipeline`, `optimize_lance_tables`) emit
      `kind=optimize`.
- [ ] `Spinner`, `emit_vectors_start`, `emit_vectors_finish` removed; no remaining
      callers/imports.
- [ ] Operator commands render the full `Vectors → Optimize → Graph` list in default
      TTY mode.
- [ ] All 8 PR-3 tests pass (heavy-gated ones skip without the env var); `ruff` +
      full `pytest tests -v` green.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Vectors emission helper + per-file ticks + approximate total | `java_index_flow_lancedb.py` | test 1 (heavy) passes |
| 2 | Optimize emission at both call sites | `server.py`, `lance_optimize.py` | test 7 passes |
| 3 | Retire `Spinner` + `emit_vectors_*`; wire vectors/optimize tasks | `pipeline.py`, `cli_progress.py`, `cli_format.py`, `cli.py`, `server.py` | test 8 passes; no dangling imports |
| 4 | Renderer clamp/indeterminate/divergence coverage | `tests/` | tests 2–6 pass |
| 5 | Full suite green | — | `ruff` + `pytest tests -v` pass |

---

# PR-4 — installer alignment (`install`/`update`) + verbosity wiring + docs

**Branch:** `feat/index-installer-progress` off `master` **after PR-3 merged**.

## File-by-file changes

### 1. `java_codebase_rag/installer.py`
- `run_init_if_needed`: replace the plain `print("Creating index…")` /
  `print("Index created successfully.")` chatter around the indexing calls with the
  renderer context (vectors + optimize + graph tasks); drop `quiet=` silence so the
  subprocess calls engage the renderer; keep the wizard's other prompts/summaries on
  stdout unchanged.
- `run_update`: drop `quiet=True` on `run_cocoindex_update` / `run_incremental_graph`;
  wrap those calls in the renderer context (not `_run_with_pipeline_progress`); accept
  and forward `quiet`/`verbose`.
- Replace the stdout `print("\nUpdating index (Lance + graph)…")` / error prints with
  stderr renderer framing where they describe indexing progress.

### 2. `java_codebase_rag/cli.py`
- `_cmd_update`: forward `quiet=bool(args.quiet)` and `verbose=bool(args.verbose)` to
  `run_update` (both are ignored today).
- `_cmd_install`: forward `verbose=bool(args.verbose)` to `run_install` (only `quiet`
  is wired today).

### 3. `docs/JAVA-CODEBASE-RAG-CLI.md`
- Document the unified progress output (header / phase list / footer on stderr), the
  determinate-vs-indeterminate behaviour per command, and the
  `--quiet` / `--verbose` / non-TTY behaviour.
- Note `install`/`update` now emit indexing progress on stderr (behaviour change) and
  that their stdout wizard text is otherwise unchanged.

### 4. `README.md`
- One-line note in the lifecycle section that indexing shows a progress bar; mention
  the `rich` dependency.

### 5. `tests/test_installer.py` and `tests/test_java_codebase_rag_cli.py`
- Installer/CLI tests asserting progress reaches stderr and stdout is preserved.

## Tests for PR-4

1. `test_install_emits_indexing_progress_on_stderr` (patch the pipeline helpers to
   emit known `JCIRAG_PROGRESS` lines; assert they reach stderr; wizard stdout
   prompts still present on stdout)
2. `test_update_emits_indexing_progress_on_stderr` (symmetric; asserts `update` is no
   longer silent)
3. `test_update_runs_indexing_without_quiet_true` (assert `run_cocoindex_update` /
   `run_incremental_graph` are called with `quiet=False` in the default path)
4. `test_cmd_update_forwards_quiet_flag` (`_cmd_update --quiet` ⇒ `run_update(quiet=True)`)
5. `test_cmd_update_forwards_verbose_flag` (`_cmd_update --verbose` ⇒ `run_update(verbose=True)`)
6. `test_cmd_install_forwards_verbose_flag`
7. `test_install_update_stdout_contract_preserved` (the wizard's human-readable stdout
   shape is unchanged; no `JCIRAG_PROGRESS` line leaks to stdout)

## Definition of done (PR-4)

- [ ] `install`/`update` render the unified phase list on stderr during indexing; the
      wizards' stdout text is otherwise unchanged.
- [ ] `update` no longer runs indexing with `quiet=True`.
- [ ] `--quiet`/`--verbose` wired through `_cmd_update`/`run_update`; `--verbose`
      through `install`.
- [ ] All 7 PR-4 tests pass; `ruff` + full `pytest tests -v` green.
- [ ] CLI docs + README updated.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Un-silence + renderer context in `run_init_if_needed` / `run_update` | `installer.py` | tests 1–3 pass |
| 2 | Forward verbosity flags | `cli.py` | tests 4–6 pass |
| 3 | stdout-contract regression | `tests/` | test 7 passes |
| 4 | Docs | `docs/JAVA-CODEBASE-RAG-CLI.md`, `README.md` | docs match behaviour |
| 5 | Full suite green | — | `ruff` + `pytest tests -v` pass |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | CocoIndex suppresses flow-function stderr → PR-3's vectors ticks never arrive | High | **Spike gate before PR-1.** If it fails, halt and re-propose the transport (file-tail is not a committed fallback). |
| 2 | Two concurrent stderr writers (relay `buffer.write` + `rich` `Live`) corrupt the display | High | `ProgressRelay` routes non-progress lines through `console.print` while `Live` is up; raw relay only in `--verbose`. Test `test_progress_relay_parses_split_chunk_once`. |
| 3 | Vectors pre-walk overstates the total (two-layer ignore + early-returns) | Medium | Bar clamps to 100% on `status=done`; divergence test (`test_pre_walk_total_divergence_bounded`) sizes the gap; authoritative-count-from-flow is the documented escalation. |
| 4 | Graph pass-1 count-first doubles the walk cost | Low | Count step is a metadata-only `os.walk` with ignore filtering (no parse); negligible vs. the parse loop. |
| 5 | Missing `cocoindex`/builder binary (126/127) leaves a task hung at `running` | Medium | Task marked `running` only after `Popen` spawns; stubs emit `status=failed`. Test in PR-1 renderer suite + PR-2/3 wiring. |
| 6 | Retiring `Spinner`/`emit_vectors_*` leaves a dangling import | Medium | PR-3 test `test_spinner_removed_and_emit_vectors_helpers_removed` + full-suite import check. |
| 7 | PR-2/PR-3/PR-4 all touch the operator-command renderer context → merge conflicts | Medium | Strict landing order (PR-2 → PR-3 → PR-4); each PR rebase-merges the previous. |
| 8 | `update` verbosity wiring changes observable behaviour for existing scripts | Low | `--quiet`/`--verbose` already existed on the parser (ignored); wiring them through matches siblings, no new flags. |
| 9 | Heavy (cocoindex/torch) tests added to the default suite slow CI / break the segfault-isolation work | Medium | All cocoindex-flow tests gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1`; parser/renderer/CLI tests use patched helpers and stay light. |

# Out of scope

- Switching the vectors phase to in-process `cocoindex` `app.update()`/`watch()`.
- A determinate denominator for incremental catch-up (would require diffing the memo store).
- Parallelising the two phases.
- Giving `install`/`update` a machine-readable stdout JSON payload.
- Splitting `init`/`increment` the way `reprocess` is split.
- Drift detection between Lance and LadybugDB stores.
- Any schema/ontology/enrichment change.

# Whole-plan done definition

1. All five lifecycle commands render the unified header / phase-list / footer on
   stderr during indexing; `init`/`increment`/`reprocess` keep their stdout payload
   and `install`/`update` keep their wizard stdout.
2. Both phases render real progress — graph determinate (exact count-first total),
   vectors determinate-approximate on full reprocess (clamp on completion) and
   indeterminate on catch-up; optimize surfaced (auto-collapsing when sub-second).
3. `JCIRAG_PROGRESS` is the single cross-process protocol; `ProgressRelay` enforces
   the parse-first / single-writer invariants; split-chunk and missing-binary cases
   are covered.
4. `Spinner` and `emit_vectors_start`/`_finish` are gone; `--quiet`/`--verbose` are
   wired through `update`/`install`.
5. `ruff` + full `pytest tests -v` green with no new heavy tests in the default suite;
   heavy-gated vectors/divergence tests pass under `JAVA_CODEBASE_RAG_RUN_HEAVY=1`.

# Tracking

- Spike: **GO — passed 2026-06-14** (130/130 `JCIRAG_PROGRESS` lines relayed to parent stderr; pre-walk divergence 0 on the fixture)
- `PR-1`: _pending_
- `PR-2`: _pending_
- `PR-3`: _pending_
- `PR-4`: _pending_
