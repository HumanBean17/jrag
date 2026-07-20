> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# INDEX-OUTPUT-REWORK — Unified progress-bearing index-build output across `init` / `increment` / `install` / `reprocess` / `update`

## Status
Proposal — not yet implemented. Design aligned in brainstorming session 2026-06-14;
revised after an adversarial self-review; **gating spike PASSED 2026-06-14**.

The load-bearing assumption — that CocoIndex does not suppress/buffer stderr written
from inside `@coco.fn` flow functions — is **confirmed**. A throwaway spike emitted
one `JCIRAG_PROGRESS` line from `process_java_file` and ran `cocoindex update` on
`tests/bank-chat-system`: 130 lines reached captured stderr (cocoindex exit 0), zero
suppression/buffering. Pre-walk divergence measured **0** on the fixture (130
non-ignored `.java` files == 130 processed). The plan's PR-1 is unblocked; the
clamp-on-completion safeguard (§ Vectors phase) remains for the general case.

## Problem Statement

All five lifecycle commands build the index through the same two subprocesses —
`cocoindex update` (vectors → Lance) and `build_ast_graph.py` (graph → LadybugDB) —
but their **output during that build differs per command**, and **none shows real
progress**.

Today's behaviour:

| Command | Header/footer | Phase markers | Progress stream | Spinner / bar |
|---|---|---|---|---|
| `init` | ✓ `_run_with_pipeline_progress` | ✓ `[vectors]` / `[graph]` | **stderr** (stdout = JSON payload) | ✓ braille spinner (TTY only) |
| `increment` | ✓ | ✓ `[vectors]` / `[increment]` | stderr | ✓ spinner (TTY only) |
| `reprocess` | ✓ | mixed per-mode paths | stderr | partial |
| `install` | ✗ none | ✗ none | **stdout** via plain `print()` | ✗ |
| `update` | ✗ none | ✗ none (`run_…(quiet=True)`!) | stdout via `print()` | ✗ (silent) |

Two concrete problems:

1. **Inconsistent, sometimes missing output.** `install` and `update` are the
   outliers: they bypass the shared `_run_with_pipeline_progress` framing, write
   indexing chatter to **stdout** (breaking the "stdout = machine-readable payload,
   stderr = human progress" contract the other three honour), and `update` runs the
   whole indexing step with `quiet=True` — completely silent. Operators cannot tell
   whether `update` is still working or hung.
2. **No progress, only elapsed time.** Every command's "progress" is either a
   braille spinner with an elapsed-seconds counter or nothing. There is no
   percentage, no items-done / items-total, no ETA. On a large tree the vectors
   phase alone can run for minutes with the operator staring at `⠹ cocoindex update · 42s`.

User asks this proposal addresses:

- **Align** the index-build output across all five commands and make it beautiful
  and user-friendly.
- **Add a progress bar** to the indexing process.

## Proposed Solution

One shared rendering path drives the index-build output for **all five commands**.
Both subprocesses emit structured progress lines the parent parses and feeds to a
single `rich`-based renderer.

### Design principles

1. **One renderer, one code path.** The operator commands render the indexing step
   via `_run_with_pipeline_progress` (`cli.py`); `install`/`update`'s installer
   call sites (`installer.run_init_if_needed`, `installer.run_update`) already
   invoke the same `run_cocoindex_update` / `run_build_ast_graph` helpers, so they
   are **un-silenced** (drop `quiet=True`, pass the progress context) to engage the
   same renderer for just the indexing sub-step — not wrapped around the whole
   wizard. No per-command output code.
2. **Progress is observable from inside the subprocesses.** The vectors phase emits
   ticks from inside `process_java_file` / `process_sql_file` / `process_yaml_file`
   (our code, called once per file by CocoIndex); the graph phase emits ticks from
   `build_ast_graph.py` (our code, run as a subprocess). We control both sides of
   the protocol.
3. **Determinate where the denominator is knowable; indeterminate where it isn't.**
   A full reprocess can *approximate* the file count up front (see §Vectors phase —
   it is approximate, not exact) → `%` bar that clamps to 100% on completion.
   Incremental catch-up only sees changed files (CocoIndex `memo=True` cache) →
   indeterminate pulsing bar with a "files touched: N" counter. Honest, never fake.
4. **stderr is for humans; stdout is the payload.** The renderer writes to stderr.
   `init`/`increment`/`reprocess` keep their stdout JSON/pprint payload unchanged.
   `install`/`update` keep their human-readable wizard stdout; only their indexing
   progress moves off stdout onto the stderr renderer.
5. **Three verbosity tiers, preserved.** `--quiet` suppresses the whole progress
   stream (payload unchanged); default is the rich display; `--verbose` relays raw
   subprocess output verbatim (as today) for debugging.
6. **Subprocess isolation is not touched.** CocoIndex stays a subprocess
   (`cocoindex update <flow>`). The deliberate isolation exists because native
   lance/pyarrow worker threads crash on interpreter shutdown (that is why
   `_console_script_main` calls `os._exit`). Switching to the in-process
   `app.update()` API — which *does* expose `handle.watch()` progress — would
   re-introduce that instability and is explicitly out of scope.

### The progress protocol (`JCIRAG_PROGRESS`)

A single, deliberately-prefixed line format so it cannot collide with relayed noise
and so the existing `_LineFilter` noise matcher is unaffected:

```
JCIRAG_PROGRESS  kind=vectors  phase=java  done=842  total=1240  status=running
JCIRAG_PROGRESS  kind=graph    pass=3/6    done=1204  total=1204  status=running
JCIRAG_PROGRESS  kind=optimize status=running
JCIRAG_PROGRESS  kind=vectors  status=done  done=1240  total=1240  elapsed_s=42.1
```

Fields: `kind` ∈ {`vectors`,`graph`,`optimize`}; `phase` (java/sql/yaml) optional;
`pass` = `N/6` for graph; `done`/`total` for determinate; `status` ∈
{`running`,`done`,`failed`}; `elapsed_s` on completion. The parent's subprocess
stderr-drain thread already exists (`pipeline._popen_capturing_stderr`,
`cli_progress.accumulate_and_relay_subprocess_streams`); it gains a parser that,
for each `JCIRAG_PROGRESS` line, calls into the renderer and **does not relay the
raw line** to the terminal (it is consumed, not displayed).

**Parse/relay ordering invariant.** Per complete line (the existing line-buffering
already reassembles lines split across `read()` chunks): the parser runs *first*; if
the line is `JCIRAG_PROGRESS` it updates the renderer and is suppressed from the
relay; otherwise the existing `_LineFilter` noise/relay path runs unchanged. A
regression test covers a progress line split across two chunks.

**Single stderr writer.** `rich`'s `Live` region and the relay thread both target
stderr; two concurrent raw writers corrupt the display (`redirect_stderr=False`
only stops `rich` capturing Python's `sys.stderr`; it does not serialize the relay).
So while the `Live` region is active, the relay thread routes every non-progress
line through `rich`'s `console.print(...)` (which reprints the live region cleanly)
instead of writing raw bytes to `sys.stderr.buffer`. Raw `buffer.write` relay runs
only in `--verbose` mode, where there is no `Live` region.

### Vectors phase

`process_java_file` / `process_sql_file` / `process_yaml_file` increment a shared
counter per file (thread-safe — whether `coco.mount_each` parallelizes is part of
the gating spike) and print a `JCIRAG_PROGRESS` line every Nth file and on completion.

- **Denominator (full reprocess — `init`, `reprocess` default, `reprocess --vectors-only`):**
  **approximate, not exact.** A pre-walk reproduces the matcher's include globs
  (`**/*.java`, `…/migration/*.sql`, `application*.yml`) plus the layered-ignore
  logic, but CocoIndex applies *two* filtering layers — the `PatternFilePathMatcher`
  excludes at walk time, then `LayeredIgnore.is_ignored()` plus an early-return for
  empty / undecodable files *inside* each `process_*_file`
  (`java_index_flow_lancedb.py:181,245,289`). Files that early-return never tick, so
  a pre-walk overstates the total by the ignored/empty count. The bar therefore
  **clamps to 100% on the `status=done` line** rather than asymptoting. The spike
  includes a divergence test (pre-walk total vs. actual `done` at completion) to size
  the gap; if it is large, the alternative is an authoritative count emitted *from
  inside the flow* (Open Q3).
- **Denominator (incremental catch-up — `increment`, `update`, `increment --vectors-only`):**
  CocoIndex's `@coco.fn(memo=True)` cache means the per-file function is only called
  for changed files, so the total is unknown up front. → `total=None`, rich's
  indeterminate pulsing bar, plus a "files touched: N" counter.
- **ETA:** derived by rich from the rate of `advance` calls (`TimeRemainingColumn`).

### Graph phase

`build_ast_graph.py` already emits `[graph] pass N` heartbeats in verbose mode; we
add the structured `JCIRAG_PROGRESS kind=graph` line. The graph builder applies
ignore filtering in a **single** layer (`iter_java_source_files(root, ignore=…)`),
so — unlike the vectors phase — its denominator is exact.

- **Pass 1 (file parse):** today `pass1_parse` walks `iter_java_source_files` as a
  generator and only knows the total *after* the walk
  (`build_ast_graph.py:865-910`). To get per-file `done/total`, pass 1 gains a cheap
  count-first step: one filtered `os.walk` (no parsing) to set the total, then the
  existing parse loop ticks per file. Determinate.
- **Passes 2–6:** each advances the bar by 1/6 with a pass label
  (`pass 3/6 · calls`). Determinate by construction (six known passes).

### Renderer (`rich`)

A `rich.progress.Progress` with one task per phase, writing to a stderr `Console`:

```python
progress = Progress(
    SpinnerColumn(),
    TextColumn("[bold]{task.fields[label]}"),
    BarColumn(), MofNCompleteColumn(), TaskProgressColumn(),
    TextColumn("· {task.fields[detail]}"),
    TimeRemainingColumn(),
    console=Console(stderr=True), transient=False,
)
with progress:
    v = progress.add_task("vectors",  total=1240, label="Vectors")
    o = progress.add_task("optimize", total=None, label="Optimize")  # pending
    g = progress.add_task("graph",    total=None, label="Graph")     # pending
    # relay thread parses JCIRAG_PROGRESS → progress.update(v, advance=1)
```

`rich` auto-disables to plain text when not a TTY, handles terminal width/resize,
redraw, and interrupt cleanup, and `Progress.update` is thread-safe (RLock) so the
subprocess-drain thread can feed it. `transient=False` so the final state stays
visible. `rich` lives only in the parent CLI process; the heavy native stack
(torch/pyarrow) still loads in the CocoIndex *child* subprocess, so the dep adds no
native crash surface to the parent.

**Task state must follow the subprocess, not the phase plan.** A task is marked
`running` only once the subprocess actually spawns; the pre-spawn checks (missing
`cocoindex` binary → `returncode=127` stub in `pipeline.py:168-174`; missing builder
→ `126`) emit `status=failed` and never mark the task `running`, so a missing binary
cannot leave a phase hung at `running` with no ticks.

### Concrete rendering

TTY (default):

```
java-codebase-rag init · source=/repo · index=/repo/.java-codebase-rag

  ◉ Vectors    ████████████░░░░ 842/1240 (68%) · ~18s left
  ○ Optimize   pending
  ○ Graph      pending

✓ java-codebase-rag init · finished in 86.4s
```

The `◉`/`○` state glyphs come from rich's spinner column (active vs pending task).
On a phase that is indeterminate (incremental catch-up), the bar renders as a
pulsing block and the `MofN` column shows `?`:

```
  ◉ Vectors    ◖◖◖◖  files touched: 37 · 9s
```

Non-TTY / CI (rich auto-disabled): the parser emits concise interval-based lines
(default every ~5 s + on completion) so CI logs still show progress:

```
java-codebase-rag init · source=/repo · index=/repo/.java-codebase-rag
vectors 842/1240 (68%)
vectors done · 1240 files · 42.1s
optimize done · 3.2s
graph pass 3/6 · calls
graph done · 6/6 · 31.4s
✓ java-codebase-rag init · finished in 86.4s
```

### Per-command matrix

| Command | Phases in the list | Notes |
|---|---|---|
| `init` | `Vectors` → `Optimize` → `Graph` | first-time full build |
| `increment` | `Vectors` → `Optimize` → `Graph` | `Graph` indeterminate on catch-up; `--vectors-only` → `Vectors` → `Optimize` (keeps the LadybugDB-stale warning) |
| `reprocess` (default) | `Vectors` → `Optimize` → `Graph` | the serialized Lance optimize surfaces as its own phase |
| `reprocess --vectors-only` | `Vectors` → `Optimize` | keeps the drift warning |
| `reprocess --graph-only` | `Graph` | keeps the drift warning |
| `install` | `Vectors` → `Optimize` → `Graph` | wizard conversational text stays on stdout; renderer wraps only the indexing sub-step |
| `update` | `Vectors` → `Optimize` → `Graph` | no longer runs `quiet=True`; renderer wraps the indexing sub-step |

`Optimize` is the serialized Lance compaction that already runs today after every
successful vectors phase (via `_maybe_run_serialized_optimize` / `optimize_lance_tables`).
It exposes no item count, so it is **always indeterminate**; `Vectors`/`Graph` are
determinate on full reprocess and indeterminate on incremental catch-up.

For `install`/`update`, the renderer engages **only around the indexing subprocess
calls** inside `run_init_if_needed` / `run_update` — the wizard's own prompts and
summaries keep their existing stdout output and are *not* put under the
`_pipeline_header`/`_pipeline_footer` framing (that framing wraps the indexing
sub-step, not the whole wizard). Concretely: drop `quiet=True` and pass the progress
context to the `run_cocoindex_update` / `run_build_ast_graph` calls; do not wrap
`run_install` / `run_update` in `_run_with_pipeline_progress`.

### Flags, TTY, and failure

| Mode | Behaviour |
|---|---|
| TTY (default) | rich `Live` region — the multi-line phase display above |
| Non-TTY / CI | rich auto-disabled; concise interval-based stderr lines |
| `--quiet` / `-q` | suppress the entire progress stream; stdout payload unchanged (as today) |
| `--verbose` / `-v` | bypass parsing; relay raw subprocess output verbatim (as today) |
| Phase failure | failing task renders red `✗` + clipped error; footer `✗ … (exit=N)`; stdout payload keeps its current failure shape; rich `Live` torn down cleanly (not transient) so the error stays visible |
| Never spawned (missing `cocoindex` / builder binary → 126/127 stub) | task never marked `running`; renderer emits a `status=failed` line + the existing failure payload; no hung bar |

`install` wires `--quiet` through today (not `--verbose`); `update` defines both
flags on its parser via `_add_verbosity_flags` but `_cmd_update` / `run_update`
ignore them entirely. This proposal wires `--quiet`/`--verbose` through both.

## Scope

### In scope

- New dependency: `rich` (pure-Python; transitive `pygments`; no native code → no
  crash risk). Pin in `pyproject.toml`.
- A new module (working name `java_codebase_rag/progress.py`) holding: the
  `JCIRAG_PROGRESS` parser, the `rich` renderer wrapper, the non-TTY line fallback,
  and the phase-list builder.
- `JCIRAG_PROGRESS` emission inside `process_java_file` / `process_sql_file` /
  `process_yaml_file` (`java_index_flow_lancedb.py`) + an approximate total pre-walk
  that reproduces the matcher includes + layered-ignore logic (see §Vectors phase).
- `JCIRAG_PROGRESS` emission inside `build_ast_graph.py`: a count-first step in
  pass 1 (filtered walk, no parse) for an exact total, then per-file ticks; passes
  2–6 per-pass.
- Wire the renderer into all five commands' indexing steps; **un-silence** the
  subprocess calls in `installer.run_init_if_needed` / `installer.run_update`
  (drop `quiet=True`, pass the progress context) — do not wrap the wizards.
- Retire the now-redundant per-command markers (`emit_vectors_start`/`_finish`, the
  inline `[graph] done` / `[increment] done` prints) in favour of the renderer.
- Wire `--quiet`/`--verbose` through `_cmd_update` / `run_update` (both flags exist
  on `update`'s parser today but are ignored entirely) and `--verbose` through
  `install` (which wires only `--quiet` today).

### Files affected

| File | Change |
|---|---|
| `pyproject.toml` | add `rich` dependency |
| `java_codebase_rag/progress.py` | **new** — parser + rich renderer + non-TTY fallback + phase-list builder |
| `java_codebase_rag/cli_format.py` | `Spinner` retired (its only caller is `pipeline.py:210`, the vectors phase); ANSI helpers reused by non-TTY fallback |
| `java_codebase_rag/cli_progress.py` | `emit_vectors_start`/`_finish` retired; subprocess-drain threads gain a `JCIRAG_PROGRESS` consumer |
| `java_codebase_rag/pipeline.py` | `_popen_capturing_stderr` + the async drain in `cli_progress` parse `JCIRAG_PROGRESS` and feed the renderer instead of (or alongside) relaying |
| `java_codebase_rag/cli.py` | `_run_with_pipeline_progress` drives the renderer; `_cmd_init`/`_cmd_increment`/`_cmd_reprocess` pass phase lists |
| `java_codebase_rag/installer.py` | `run_init_if_needed`, `run_update`: un-silence the `run_cocoindex_update` / `run_build_ast_graph` calls (drop `quiet=True`, pass progress context); drop plain `print()` indexing chatter |
| `server.py` | `run_refresh_pipeline`'s vectors phase feeds the same renderer (reprocess default path); **two** optimize call sites — `run_refresh_pipeline` (`server.py:359-372`) and `_maybe_run_serialized_optimize` (`pipeline.py:129`) — both emit `JCIRAG_PROGRESS kind=optimize` for consistent phase display |
| `java_index_flow_lancedb.py` | `JCIRAG_PROGRESS` emission in the three `process_*_file` functions + approximate total pre-walk |
| `build_ast_graph.py` | count-first step in pass 1 (exact total) + per-file `JCIRAG_PROGRESS`; passes 2–6 per-pass |
| `docs/JAVA-CODEBASE-RAG-CLI.md` | document the new progress output + non-TTY/`--quiet`/`--verbose` behaviour |
| `README.md` | note `rich` dep + one-line progress mention if the lifecycle section warrants it |

## Schema / Ontology / Re-index impact

- **Ontology bump:** not required. No graph/edge semantic change; `ontology_version`
  stays at 17.
- **Re-index required:** no. Pure output/UX change; index artefacts and payload
  shapes are unchanged.
- **Config/tool surface changes:** none. No new env vars, no new CLI flags. The
  `--quiet`/`--verbose` flags already exist on every lifecycle subparser; this wires
  the (currently ignored) ones on `update`/`install` through. Flag semantics are
  preserved, not redefined.

## Tests / Validation

Per `tests/README.md` — assert invariants, not exact formatting; never special-case
the `tests/bank-chat-system/` fixture.

- **Protocol contract:** new tests assert a `JCIRAG_PROGRESS` line written from
  inside a flow function reaches the parent's captured stderr (the gating spike,
  promoted to a regression test).
- **install/update now emit progress:** new tests assert `install`/`update` emit
  indexing progress on **stderr** during the indexing step (today they emit nothing
  on stderr); their stdout machine-readable shape (where present) is unchanged.
- **Operator commands unchanged payload:** `init`/`increment`/`reprocess` keep their
  stdout JSON/pprint payload exactly; new tests pin that no `JCIRAG_PROGRESS` line
  leaks onto stdout.
- **Regression anchor:** `increment` (no flag, full path) must NOT emit the
  LadybugDB-stale warning, while `increment --vectors-only` must STILL emit it
  (`cli.py:321-324`); `reprocess --vectors-only` / `--graph-only` drift warnings
  still fire.
- **Verbosity tiers:** `--quiet` produces no progress stderr; `--verbose` relays
  raw subprocess output (assert a known raw line passes through unfiltered).
- **Failure:** a failing phase renders a non-zero exit + error payload on stdout;
  the renderer does not mask it.
- **Never-spawned:** missing `cocoindex`/builder binary (126/127 stub) emits
  `status=failed` and leaves no task hung at `running`.
- **Protocol robustness:** a `JCIRAG_PROGRESS` line split across two `read()` chunks
  parses once; a non-progress line is relayed/printed unchanged.
- **Vectors divergence (spike):** on the fixture, pre-walk total vs. actual `done` at
  completion differ only by the ignored/empty count; the bar clamps to 100%.
- **Validation gate (AGENTS.md):** `.venv/bin/ruff check .` + `.venv/bin/python -m
  pytest tests -v` (heavy e2e gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1`, as
  usual). Manual hello-world: `rm -rf /tmp/check && .venv/bin/python build_ast_graph.py
  --source-root tests/bank-chat-system --ladybug-path /tmp/check/code_graph.lbug
  --verbose` to confirm graph-phase progress lines render.

## Open Questions ([TBD])

1. Tick cadence for the vectors phase — every file, every N files, or time-based?
   — Recommended: **every N files (N≈25) + on completion**, to bound stderr volume
   on huge trees without making the bar feel stale.
2. Graph pass-1 determinacy — count-first (filtered walk for an exact total, then
   parse) or render indeterminate during the walk? — Recommended: **count-first**
   (the graph's single-layer ignore makes the count exact and cheap).
3. Vectors total accuracy — accept the approximate pre-walk + clamp-on-completion,
   or emit an authoritative `total` from inside the flow (a preflight running
   `is_ignored` + content checks)? — Recommended: **approximate + clamp** for v1;
   escalate to the authoritative count only if the spike's divergence test shows a
   large gap.
4. Should `Optimize` get its own row, or collapse into the vectors tail
   (`Vectors → optimizing…`)? On small repos optimize is sub-second and a third row
   is noise. — Recommended: **own row, auto-collapse to a vectors sub-state when the
   phase completes under ~1 s**.
5. Non-TTY concise-line cadence — time-based or count-based? — Recommended:
   **time-based, every ~5 s + on phase completion** (matches CI log rhythm).
6. Should `install`/`update` gain a stdout JSON payload like the operator commands
   for full scriptability parity? — Recommended: **no** for v1. They are interactive
   wizards; their human-readable stdout is the point. Revisit if a real automation
   ask surfaces.
7. `rich` version pin width? — Recommended: **`rich>=14,<15`**
   (`cocoindex[lancedb]>=1.0.0a43` transitively requires `rich>=14`, so the
   `>=13.7,<14` cap originally suggested is unsatisfiable; verified compatible with
   the renderer's `Progress` API usage on rich 14.3.4).

## Decisions taken

1. **Determinate for both phases where the denominator is knowable.** The graph
   phase is exactly determinate (count-first pass 1; six known passes). The vectors
   phase is *approximately* determinate on full reprocess (pre-walk overstates by
   the ignored/empty count; bar clamps to 100%) and indeterminate on catch-up.
2. **Renderer:** `rich` (not hand-rolled). Chosen for polish, thread-safe updates,
   auto non-TTY fallback, and ~50–80 lines of glue vs. ~250–350 of fiddly ANSI.
3. **CocoIndex stays a subprocess.** In-process `app.update()`/`watch()` would give
   first-class progress but re-introduces the shutdown native-thread crash the
   subprocess isolation exists to avoid. Out of scope.
4. **stderr for the renderer; each command keeps its stdout contract.** Alignment is
   the indexing UX, not the commands' stdout surface.
5. **`JCIRAG_PROGRESS` structured lines** as the cross-process channel. A
   progress-file tail is *not* a committed fallback — if the gating spike shows
   CocoIndex suppresses flow-function stderr, this proposal pauses for a transport
   re-design rather than ship a racy file-tail with the same denominator problem.
6. **Three verbosity tiers preserved** (`--quiet` / default / `--verbose`).

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| CocoIndex suppresses/buffers stderr written from inside `@coco.fn` flow functions → vectors ticks never reach the parent | **Throwaway spike branch first** (no PR): emit one `JCIRAG_PROGRESS` line from `process_java_file` and confirm the parent sees it. If it does not, **halt and re-propose the transport** — a progress-file tail is *not* a committed fallback (it re-introduces the denominator problem plus a writer/tailer race). Nothing else lands until the spike settles. |
| `rich` adds a dependency the repo has avoided | Pure-Python, parent-process only (the heavy native stack still loads in the CocoIndex *child* subprocess, unaffected); pulls only `pygments`. `rich.progress` is stable across 14.x. Pinned `>=14,<15` (forced by `cocoindex[lancedb]`'s `rich>=14` requirement). Acceptable for a CLI dev tool whose explicit goal is beautiful output. |
| `memo=True` makes the incremental denominator unknown → bar looks "stuck" at indeterminate | Intended behaviour, not a bug: indeterminate pulsing bar + "files touched: N" counter is honest. Documented in §Vectors phase. |
| Per-file `%` over-/under-reports because files have very different chunk counts (embedding cost) | The bar reports *files* done, not embedding cost — accurate as a file counter. ETA comes from rich's rolling rate, which absorbs chunk-count variance across many files. Acceptable approximation. |
| Two concurrent stderr writers (relay thread's raw `sys.stderr.buffer.write` + `rich` `Live`) interleave/corrupt the display | While the `Live` region is active, the relay thread routes every non-progress line through `console.print(...)` (rich reprints the live region cleanly) — never raw `buffer.write`. Raw relay runs only in `--verbose`, where there is no `Live` region. Existing `_LineFilter` noise matcher still drops `lance::`/`FutureWarning`/brownfield noise before `console.print`. Regression test: a `JCIRAG_PROGRESS` line split across two `read()` chunks parses once. |
| `Live` region torn down uncleanly on `SIGINT`/`KeyboardInterrupt` | rich handles `KeyboardInterrupt` in its `Live` context manager; the phase that was interrupted renders `✗`. Verify in the spike. |
| Missing `cocoindex`/builder binary → subprocess never spawns → phase hung at `running` with no ticks | Pre-spawn stub (`pipeline.py:168-174`) emits `status=failed`; a task is marked `running` only after the subprocess spawns, so the stub leaves no hung bar. Regression test covers 126/127. |
| Tiny repo: a phase finishes before its first tick → bar flashes pending→done | Harmless; the `status=done` line still renders. Spike covers a 3-file run to confirm no empty-bar flicker. |
| Tests over-assert on ANSI/rich formatting and become brittle | Assert on `JCIRAG_PROGRESS` contract + payload invariants, never on ANSI codes or exact column layout (per `tests/README.md`). |

## Out of scope

- Switching the vectors phase to in-process `cocoindex` `app.update()`/`watch()`
  (stability risk; see principle 6).
- Splitting `init` or `increment` the way `reprocess` is split (no use case; see
  `REPROCESS-SPLIT-PROPOSE` decisions).
- A determinate denominator for incremental catch-up (would require diffing against
  CocoIndex's memo store — non-trivial, separate propose).
- Drift detection between Lance and LadybugDB stores (separate propose).
- Parallelising the two phases when both run (separate perf propose).
- Giving `install`/`update` a machine-readable stdout JSON payload (see Open Q4).
- Keeping the hand-rolled `Spinner` for any other caller (there are none — its only
  caller is the vectors phase, so it is fully retired here).

## Sequencing / Follow-ups

Multi-PR; suggested split (a matching `plans/active/PLAN-INDEX-OUTPUT-REWORK.md`
follows once the propose is approved):

- **Spike (throwaway branch, no PR):** confirm CocoIndex relays flow-function
  stderr; size the pre-walk divergence. Gate. If it fails, halt and re-propose the
  transport.
- **PR-1 (protocol + renderer skeleton):** only if the spike passes — land the
  `JCIRAG_PROGRESS` parser + `rich` renderer + non-TTY fallback + the
  parse/relay/single-writer invariants, with unit tests (split-chunk,
  missing-binary). Ship the `rich` dep here. No command wiring yet.
- **PR-2 (graph phase):** count-first pass 1 + per-file `JCIRAG_PROGRESS`, passes
  2–6 per-pass; wire the renderer into the graph phase of all five commands.
- **PR-3 (vectors phase):** `JCIRAG_PROGRESS` from `process_*_file` + approximate
  total pre-walk (clamp-on-completion); wire the renderer into the vectors phase
  (incl. the serialized `Optimize` phase — both call sites). Retire
  `emit_vectors_start`/`_finish`.
- **PR-4 (installer alignment + docs):** un-silence the subprocess calls in
  `run_init_if_needed` / `run_update` (drop `quiet=True`, pass progress context);
  wire `--quiet`/`--verbose` through `_cmd_update`/`run_update` and `--verbose`
  through `install`; update `docs/JAVA-CODEBASE-RAG-CLI.md`.

Each PR independently green (`ruff` + `pytest tests -v`).
