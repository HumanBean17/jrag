# Agent task prompts — INDEX-OUTPUT-REWORK (Spike → PR-4)

Status: **active**. Plan:
[`plans/active/PLAN-INDEX-OUTPUT-REWORK.md`](./PLAN-INDEX-OUTPUT-REWORK.md). Propose:
[`propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md`](../../propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md).

One prompt per step. **Landing order:** Spike (gate) → PR-1 → PR-2 → PR-3 → PR-4. Do
not start the next step until the previous is merged to `master` (the Spike is not
merged — it is a throwaway go/no-go).

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only. Never system `python`/`pip`.
- Nothing reachable from MCP tool handlers may write to **stdout** (stderr is the
  progress stream; stdout is the JSON/wizard payload).
- **No ontology bump.** `ontology_version` stays **17**. No schema/enrichment change.
- **No new CLI flags** (existing `--quiet`/`--verbose` are wired through; nothing added).
- If ambiguous versus the plan/propose, **stop and ask** — do not expand scope.
- Do not `git push` unless the user explicitly asked.
- No drive-by lint fixes outside deliverables.
- CocoIndex stays a **subprocess**. Do not switch to in-process `app.update()`/`watch()`.

---

## Spike — validate CocoIndex flow-function stderr relay (gate, no PR)

**Branch:** throwaway (e.g. `spike/flow-stderr`), **not merged**.
**Plan section:** `plans/active/PLAN-INDEX-OUTPUT-REWORK.md` § Spike.

**Attach (`@-files`):**

- `@plans/active/PLAN-INDEX-OUTPUT-REWORK.md` (Spike + Principles only)
- `@propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md` (§Vectors phase, Risks)
- `@java_index_flow_lancedb.py`
- `@java_codebase_rag/pipeline.py`
- `@path_filtering.py` (for the pre-walk divergence step)

**Prompt:**

````
You are running the gating spike for INDEX-OUTPUT-REWORK. This is a throwaway branch;
it is NOT merged. The whole plan is contingent on this.

## Objective
Confirm CocoIndex relays stderr written from inside `@coco.fn` flow functions to the
parent, and size the vectors pre-walk divergence.

## Steps
1. In `java_index_flow_lancedb.py`, at the top of `process_java_file` (before the
   ignore check), emit one line:
   `print("JCIRAG_PROGRESS kind=vectors phase=java done=0 total=0 status=running", file=sys.stderr, flush=True)`.
2. Run `cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance --full-reprocess`
   against `tests/bank-chat-system` with stdout+stderr captured to pipes (mirror
   `pipeline._popen_capturing_stderr`'s capture). Heavy: this loads torch — it is fine
   for the spike.
3. Inspect captured stderr for the `JCIRAG_PROGRESS` line.
4. Pre-walk `tests/bank-chat-system` reproducing the matcher includes + `LayeredIgnore`
   (`cocoindex_excluded_patterns()` + `is_ignored`); record the count vs. the actual
   `done` at completion.

## Done when
- A one-paragraph **go/no-go note** is written on the throwaway branch:
  - GO: "stderr relays — proceed with PR-1" + the divergence number (e.g. "pre-walk
    42, done 40, gap 2 ignored/empty").
  - NO-GO: "stderr suppressed — halt, re-propose transport".
- Do NOT open a PR. Report the verdict back; the branch is discarded (or kept as a
  reference if NO-GO).
````

---

## PR-1 — `rich` dep + `progress.py` (parser, renderer, non-TTY fallback, relay)

**Branch:** `feat/index-progress-protocol` off `master` **only after the Spike is GO**.
**Base:** `master`.
**Plan section:** `plans/active/PLAN-INDEX-OUTPUT-REWORK.md` § PR-1.
**PR title:** `feat(cli): add rich + progress protocol/renderer skeleton (JCIRAG_PROGRESS)`

**Attach (`@-files`):**

- `@plans/active/PLAN-INDEX-OUTPUT-REWORK.md` (PR-1 + Resolved design decisions only)
- `@propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md` (§ progress protocol, § renderer, Risks)
- `@pyproject.toml`
- `@java_codebase_rag/cli_format.py` (ANSI helpers reused by non-TTY fallback)
- `@java_codebase_rag/cli_progress.py` (existing `_AsyncLineFilter` / drain — reference only)
- `@java_codebase_rag/pipeline.py` (existing `_LineFilter` / drain — reference only)

**Prompt:**

````
You are implementing PR-1 from `plans/active/PLAN-INDEX-OUTPUT-REWORK.md`. Read the
**PR-1** section and the **Resolved design decisions** table before coding. Plan wins
over this prompt.

## Scope
1. Add `rich>=14,<15` to `pyproject.toml` `dependencies` (`cocoindex[lancedb]>=1.0.0a43` requires `rich>=14`; a `<14` cap is unsatisfiable).
2. Create `java_codebase_rag/progress.py` with exactly these symbols:
   - `ProgressEvent` dataclass (`kind`, `phase`, `pass_`, `done`, `total`, `status`,
     `elapsed_s`).
   - `parse_progress_line(line: bytes) -> ProgressEvent | None` — `None` for any
     non-`JCIRAG_PROGRESS` line; never raises.
   - `IndexProgressRenderer` — `rich.progress.Progress` (TTY) / concise-line fallback
     (non-TTY); one task per phase; task visible/`running` only after its first event;
     `apply(ev)` clamps completed→total on `status=done`; marks red on `status=failed`;
     non-TTY prints at most every ~5 s per phase + on terminal events.
   - `ProgressRelay` — line-buffered `feed(chunk)`; parse-first; progress events →
     `renderer.apply` and suppressed from relay; non-progress → noise matcher then
     `console.print` while `Live` is up (or raw `buffer.write` when relaying verbatim).
3. Add the 13 PR-1 tests verbatim from the plan in `tests/test_progress.py` — all
   light (no subprocess, no cocoindex, no torch).

## Out of scope (do NOT touch)
- Any `JCIRAG_PROGRESS` emission in production files (`build_ast_graph.py`,
  `java_index_flow_lancedb.py`, `lance_optimize.py`, `server.py`). PR-2/3 add those.
- Any command wiring (`cli.py`, `installer.py`). No production caller of `progress.py`
  in this PR.
- `_popen_capturing_stderr` / `accumulate_and_relay_subprocess_streams` changes
  (the `on_progress` plumbing is PR-2).
- `Spinner` / `emit_vectors_*` removal (PR-3).
- Ontology version, schema, any enrichment.

If you need any of the above, **stop and ask**.

## Deliverables
1. `rich` installed; `progress.py` with the four symbols; 13 tests in `test_progress.py`.
2. No production caller of `progress.py` yet.

## Tests
```bash
.venv/bin/ruff check java_codebase_rag/progress.py tests/test_progress.py pyproject.toml
.venv/bin/python -m pytest tests/test_progress.py -v
```
Before PR open:
```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```
Expected: all 13 PR-1 tests pass; full suite green; no new heavy gating.

## Sentinel checks (`git diff master..HEAD`)
```bash
# No production emission yet:
git diff master..HEAD -- build_ast_graph.py java_index_flow_lancedb.py lance_optimize.py server.py | rg "JCIRAG_PROGRESS" && exit 1 || true
# No production caller of progress.py yet:
git diff master..HEAD -- java_codebase_rag/cli.py java_codebase_rag/installer.py java_codebase_rag/pipeline.py | rg "import.*progress|from java_codebase_rag.progress" && exit 1 || true
# rich added:
git diff master..HEAD -- pyproject.toml | rg '^\+.*"rich' || { echo "rich dep missing"; exit 1; }
```

## Manual evidence
```bash
.venv/bin/python -c "from java_codebase_rag.progress import parse_progress_line, ProgressEvent, IndexProgressRenderer, ProgressRelay; print('symbols ok')"
```

## Definition of Done
- [ ] All 13 PR-1 test names from the plan exist and pass.
- [ ] Sentinels pass (no production emission/caller; rich dep present).
- [ ] `.venv/bin/ruff check .` + `.venv/bin/python -m pytest tests -v` green.
- [ ] PR title: `feat(cli): add rich + progress protocol/renderer skeleton (JCIRAG_PROGRESS)`
- [ ] Branch: `feat/index-progress-protocol`
````

---

## PR-2 — graph-phase progress (operator commands)

**Branch:** `feat/index-graph-progress` off `master` **after PR-1 merged**.
**Base:** `master` at PR-1 merge.
**Plan section:** `plans/active/PLAN-INDEX-OUTPUT-REWORK.md` § PR-2.
**PR title:** `feat(cli): graph-phase index progress (count-first pass1 + pass steps)`

**Attach (`@-files`):**

- `@plans/active/PLAN-INDEX-OUTPUT-REWORK.md` (PR-2 + Resolved design decisions)
- `@propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md` (§ Graph phase, § progress protocol)
- `@java_codebase_rag/progress.py` (PR-1 symbols)
- `@java_codebase_rag/pipeline.py`
- `@java_codebase_rag/cli_progress.py`
- `@java_codebase_rag/cli.py`
- `@build_ast_graph.py`
- `@tests/test_ast_graph_build.py`
- `@tests/test_java_codebase_rag_cli.py`

**Prompt:**

````
You are implementing PR-2 from `plans/active/PLAN-INDEX-OUTPUT-REWORK.md`. Read the
**PR-2** section + Resolved design decisions. Plan wins over this prompt.

## Scope
1. `build_ast_graph.py`: add `_emit_progress(parts)` writing one
   `JCIRAG_PROGRESS kind=graph …` line to stderr (flushed), gated by the existing
   verbose flag. `pass1_parse`: count-first (one filtered `os.walk`, no parse) for the
   exact total, then emit `pass=1 total=N` and a `done=k` tick every ~25 files + on
   completion. `pass2_edges`…`pass6_match_edges`: emit `pass=N/6 status=running` on
   entry, `status=done elapsed_s=…` on exit. Keep the existing heartbeat lines.
2. `pipeline.py` `_popen_capturing_stderr`: accept `on_progress` callback; replace the
   inline `_LineFilter` drain with a `ProgressRelay` (parse-first; events → callback;
   non-progress → existing noise/relay). `run_build_ast_graph` / `run_incremental_graph`:
   thread `on_progress` from the caller; `--verbose` mode passes `on_progress=None`
   (raw relay, no renderer).
3. `cli_progress.py` `accumulate_and_relay_subprocess_streams`: same `ProgressRelay` +
   `on_progress` wiring.
4. `cli.py`: a renderer context around `_run_with_pipeline_progress`'s `work()` (TTY,
   non-`--quiet`, non-`--verbose`) owning the **graph** task; mark it `running` only
   after the builder spawns; route non-progress relay lines through `console.print`.
   `_cmd_init`/`_cmd_increment`/`_cmd_reprocess` pass the graph `on_progress` through.
   Vectors/Optimize tasks stay **pending** in this PR.
5. Add the 7 PR-2 tests verbatim from the plan. Tests 5–7 patch the pipeline helpers
   (no cocoindex/torch) so they run in the default light suite.

## Out of scope (do NOT touch)
- Vectors-phase emission (`java_index_flow_lancedb.py`) — PR-3.
- Optimize emission / `Spinner` / `emit_vectors_*` removal — PR-3.
- `installer.py` (`install`/`update`) — PR-4.
- `_cmd_update`/`_cmd_install` flag forwarding — PR-4.
- Ontology version, schema, enrichment.

If you need any of the above, **stop and ask**.

## Deliverables
1. `build_ast_graph.py` emits `kind=graph` progress (exact count-first total + pass
   steps) on the non-quiet path.
2. Sync + async drains route progress events to a callback and suppress raw relay.
3. Operator commands render the graph task determinate in default TTY mode.

## Tests
```bash
.venv/bin/ruff check build_ast_graph.py java_codebase_rag/pipeline.py java_codebase_rag/cli_progress.py java_codebase_rag/cli.py tests/
.venv/bin/python -m pytest tests/test_ast_graph_build.py tests/test_java_codebase_rag_cli.py -v \
  -k "pass1_emits_per_file or pass1_total_is_exact or passes_2_to_6 or graph_quiet_emits_no_progress or graph_phase_progress_on_stderr or increment_graph_phase_progress or graph_progress_absent_when_quiet"
```
Before PR open:
```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD`)
```bash
# No vectors/optimize emission yet (PR-3):
git diff master..HEAD -- java_index_flow_lancedb.py | rg "JCIRAG_PROGRESS" && exit 1 || true
git diff master..HEAD -- java_index_flow_lancedb.py lance_optimize.py | rg "kind=vectors|kind=optimize" && exit 1 || true
# Spinner / emit_vectors_* untouched:
git diff master..HEAD -- java_codebase_rag/cli_format.py | rg "^-.*class Spinner" && exit 1 || true
git diff master..HEAD -- java_codebase_rag/cli_progress.py | rg "^-.*def emit_vectors" && exit 1 || true
# installer untouched:
git diff master..HEAD -- java_codebase_rag/installer.py | rg "JCIRAG_PROGRESS|IndexProgressRenderer|run_init_if_needed.*quiet" && exit 1 || true
# graph progress emitted:
git diff master..HEAD -- build_ast_graph.py | rg "JCIRAG_PROGRESS kind=graph" || { echo "missing graph progress"; exit 1; }
```

## Manual evidence
```bash
rm -rf /tmp/iograph && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system --ladybug-path /tmp/iograph/code_graph.lbug --verbose 2>&1 \
  | rg "JCIRAG_PROGRESS kind=graph" | head
```

## Definition of Done
- [ ] All 7 PR-2 test names pass.
- [ ] Sentinels pass.
- [ ] `install`/`update` unchanged (still `quiet=True`).
- [ ] PR title: `feat(cli): graph-phase index progress (count-first pass1 + pass steps)`
- [ ] Branch: `feat/index-graph-progress`
````

---

## PR-3 — vectors-phase progress + retire `Spinner`/`emit_vectors_*` + both optimize sites

**Branch:** `feat/index-vectors-progress` off `master` **after PR-2 merged**.
**Base:** `master` at PR-2 merge.
**Plan section:** `plans/active/PLAN-INDEX-OUTPUT-REWORK.md` § PR-3.
**PR title:** `feat(cli): vectors-phase index progress; retire Spinner/emit_vectors; optimize phase`

**Attach (`@-files`):**

- `@plans/active/PLAN-INDEX-OUTPUT-REWORK.md` (PR-3 + Resolved design decisions)
- `@propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md` (§ Vectors phase, § Risks)
- `@java_codebase_rag/progress.py`
- `@java_index_flow_lancedb.py`
- `@java_codebase_rag/pipeline.py`
- `@java_codebase_rag/cli_progress.py`
- `@java_codebase_rag/cli_format.py`
- `@java_codebase_rag/cli.py`
- `@server.py`
- `@java_codebase_rag/lance_optimize.py`
- `@tests/test_java_codebase_rag_cli.py`

**Prompt:**

````
You are implementing PR-3 from `plans/active/PLAN-INDEX-OUTPUT-REWORK.md`. Read the
**PR-3** section + Resolved design decisions + propose § Vectors phase. Plan wins.

## Scope
1. `java_index_flow_lancedb.py`: `_emit_vectors_progress(...)` writes
   `JCIRAG_PROGRESS kind=vectors …` to stderr. In `app_main`, emit an approximate
   `total=N status=running` from a pre-walk reproducing the matcher includes +
   `LayeredIgnore` (`cocoindex_excluded_patterns()` + `is_ignored`). In each
   `process_*_file`, increment an atomic counter and emit `done=k` every ~25 files +
   `status=done elapsed_s=…` on the final file. (Incremental catch-up: `memo=True`
   ⇒ function only called for changed files ⇒ no `total` event ⇒ parent renders
   indeterminate.)
2. `pipeline.py` `_run_cocoindex_update_impl`: drop the `Spinner` and
   `emit_vectors_start`/`_finish`; route vectors events through `on_progress`; mark
   the vectors task `running` only after `cocoindex` `Popen` succeeds (not on the 127
   stub).
3. `server.py` `run_refresh_pipeline`: route async-drain vectors events into the
   renderer; drop `emit_vectors_start`/`emit_vectors_finish`; the optimize block
   (`server.py:359-372`) emits `kind=optimize status=running` / `status=done`.
4. `lance_optimize.py` `optimize_lance_tables`: emit `kind=optimize status=running`
   on entry, `status=done elapsed_s=…` on exit — the **second** optimize call site
   (called from `_maybe_run_serialized_optimize` in `pipeline.py:129`).
5. `cli_progress.py`: remove `emit_vectors_start` / `emit_vectors_finish`.
6. `cli_format.py`: remove the `Spinner` class.
7. `cli.py`: extend the renderer context to own **vectors** + **optimize** tasks for
   the operator commands (phase order `Vectors → Optimize → Graph`).
8. Add the 8 PR-3 tests verbatim from the plan. The cocoindex-flow tests
   (`test_flow_emits_vectors_progress_per_file`, `test_pre_walk_total_divergence_bounded`)
   are **heavy-gated** (`JAVA_CODEBASE_RAG_RUN_HEAVY=1`); the rest stay light via
   patched helpers / synthetic event streams.

## Out of scope (do NOT touch)
- `installer.py` (`install`/`update`) — PR-4.
- `_cmd_update`/`_cmd_install` flag forwarding — PR-4.
- Switching to in-process `cocoindex` `app.update()`/`watch()`.
- Ontology version, schema, enrichment.
- Any new CLI flag.

If you need any of the above, **stop and ask**.

## Deliverables
1. `process_*_file` emit `kind=vectors` progress; approximate total from `app_main`;
   bar clamps to 100% on completion.
2. Both optimize call sites emit `kind=optimize`.
3. `Spinner`, `emit_vectors_start`, `emit_vectors_finish` removed (no dangling imports).
4. Operator commands render `Vectors → Optimize → Graph`.

## Tests
```bash
.venv/bin/ruff check java_index_flow_lancedb.py java_codebase_rag/pipeline.py java_codebase_rag/cli_progress.py java_codebase_rag/cli_format.py java_codebase_rag/cli.py server.py java_codebase_rag/lance_optimize.py tests/
.venv/bin/python -m pytest tests -v -k "vectors_progress_clamps or vectors_progress_approximate or vectors_incremental_renders or cli_init_vectors_phase or reprocess_optimize_phase or spinner_removed or emit_vectors or flow_emits_vectors or pre_walk_total_divergence"
```
Before PR open:
```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```
Expected: all 8 PR-3 tests pass (heavy-gated skip without the env var); full suite green.

## Sentinel checks (`git diff master..HEAD`)
```bash
# Spinner + emit_vectors_* gone (import must fail):
.venv/bin/python -c "from java_codebase_rag.cli_format import Spinner" 2>/dev/null && { echo "Spinner still importable"; exit 1; } || true
.venv/bin/python -c "from java_codebase_rag.cli_progress import emit_vectors_start" 2>/dev/null && { echo "emit_vectors_start still importable"; exit 1; } || true
# No remaining callers/imports of removed symbols anywhere in the tree:
rg -n "emit_vectors_start|emit_vectors_finish|class Spinner|Spinner\(" java_codebase_rag/ server.py && exit 1 || true
# installer untouched:
git diff master..HEAD -- java_codebase_rag/installer.py | rg "JCIRAG_PROGRESS|IndexProgressRenderer" && exit 1 || true
# both optimize call sites emit kind=optimize:
git diff master..HEAD -- server.py | rg "kind=optimize" || { echo "server.py missing optimize progress"; exit 1; }
git diff master..HEAD -- java_codebase_rag/lance_optimize.py | rg "kind=optimize" || { echo "lance_optimize.py missing optimize progress"; exit 1; }
```

## Manual evidence (heavy)
```bash
JAVA_CODEBASE_RAG_RUN_HEAVY=1 rm -rf /tmp/iovec && JAVA_CODEBASE_RAG_INDEX_DIR=/tmp/iovec \
  cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance --full-reprocess 2>&1 \
  | rg "JCIRAG_PROGRESS kind=vectors" | head
```

## Definition of Done
- [ ] All 8 PR-3 test names pass (heavy-gated skip without env).
- [ ] Sentinels pass; `Spinner`/`emit_vectors_*` not importable.
- [ ] `ruff` + full `pytest tests -v` green.
- [ ] PR title: `feat(cli): vectors-phase index progress; retire Spinner/emit_vectors; optimize phase`
- [ ] Branch: `feat/index-vectors-progress`
````

---

## PR-4 — installer alignment (`install`/`update`) + verbosity wiring + docs

**Branch:** `feat/index-installer-progress` off `master` **after PR-3 merged**.
**Base:** `master` at PR-3 merge.
**Plan section:** `plans/active/PLAN-INDEX-OUTPUT-REWORK.md` § PR-4.
**PR title:** `feat(cli): unified index progress for install/update; wire --quiet/--verbose`

**Attach (`@-files`):**

- `@plans/active/PLAN-INDEX-OUTPUT-REWORK.md` (PR-4 + Resolved design decisions)
- `@propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md` (§ Per-command matrix, § Flags/TTY/failure)
- `@java_codebase_rag/progress.py`
- `@java_codebase_rag/installer.py`
- `@java_codebase_rag/cli.py`
- `@docs/JAVA-CODEBASE-RAG-CLI.md`
- `@README.md`
- `@tests/test_installer.py`
- `@tests/test_java_codebase_rag_cli.py`

**Prompt:**

````
You are implementing PR-4 from `plans/active/PLAN-INDEX-OUTPUT-REWORK.md`. Read the
**PR-4** section + Resolved design decisions + propose § Per-command matrix. Plan wins.

## Scope
1. `installer.py` `run_init_if_needed`: replace the stdout `print("Creating index…")`
   / `print("Index created successfully.")` chatter around the indexing calls with the
   renderer context (vectors + optimize + graph tasks); un-silence the
   `run_cocoindex_update` / `run_build_ast_graph` calls (drop `quiet=True`-style
   silence; pass the progress context). Keep all other wizard prompts/summaries on
   stdout unchanged. Do NOT wrap `run_install` in `_run_with_pipeline_progress`.
2. `installer.py` `run_update`: drop `quiet=True` on `run_cocoindex_update` /
   `run_incremental_graph`; wrap those calls in the renderer context (not
   `_run_with_pipeline_progress`); accept and forward `quiet`/`verbose`. Move
   stdout `print("\nUpdating index (Lance + graph)…")` / error prints that describe
   indexing progress onto the stderr renderer framing.
3. `cli.py` `_cmd_update`: forward `quiet=bool(args.quiet)` and
   `verbose=bool(args.verbose)` to `run_update` (both ignored today). `_cmd_install`:
   forward `verbose=bool(args.verbose)` to `run_install` (only `quiet` wired today).
4. `docs/JAVA-CODEBASE-RAG-CLI.md`: document the unified progress output (header /
   phase list / footer on stderr), determinate-vs-indeterminate per command, and
   `--quiet`/`--verbose`/non-TTY behaviour; note the `install`/`update` stderr
   behaviour change and that wizard stdout is otherwise unchanged.
5. `README.md`: one-line lifecycle note that indexing shows a progress bar; mention
   the `rich` dependency.
6. Add the 7 PR-4 tests verbatim from the plan (patch the pipeline helpers — no
   cocoindex/torch — so they run in the default light suite).

## Out of scope (do NOT touch)
- `build_ast_graph.py` / `java_index_flow_lancedb.py` emission (PR-2/3).
- `progress.py` symbols.
- A stdout JSON payload for `install`/`update` (Open Q6 — recommended no).
- Ontology version, schema, enrichment.
- Any new CLI flag.

If you need any of the above, **stop and ask**.

## Deliverables
1. `install`/`update` render the unified phase list on stderr during indexing; wizard
   stdout otherwise unchanged.
2. `update` no longer runs indexing with `quiet=True`.
3. `--quiet`/`--verbose` wired through `_cmd_update`/`run_update`; `--verbose` through
   `install`.
4. Docs updated.

## Tests
```bash
.venv/bin/ruff check java_codebase_rag/installer.py java_codebase_rag/cli.py docs/JAVA-CODEBASE-RAG-CLI.md README.md tests/
.venv/bin/python -m pytest tests/test_installer.py tests/test_java_codebase_rag_cli.py -v \
  -k "install_emits_indexing_progress_on_stderr or update_emits_indexing_progress_on_stderr or update_runs_indexing_without_quiet_true or cmd_update_forwards_quiet or cmd_update_forwards_verbose or cmd_install_forwards_verbose or stdout_contract_preserved"
```
Before PR open:
```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD`)
```bash
# update no longer passes quiet=True to the indexing helpers:
git diff master..HEAD -- java_codebase_rag/installer.py | rg '^\-.*quiet=True' || { echo "expected quiet=True removal in installer"; exit 1; }
git diff master..HEAD -- java_codebase_rag/installer.py | rg 'run_cocoindex_update\(.*quiet=True|run_incremental_graph\(.*quiet=True' && exit 1 || true
# flags forwarded:
git diff master..HEAD -- java_codebase_rag/cli.py | rg "verbose=bool\(args.verbose\)" || { echo "verbose not forwarded"; exit 1; }
# stdout contract: no JCIRAG_PROGRESS / IndexProgressRenderer reaches stdout:
git diff master..HEAD | rg "print\(.*JCIRAG_PROGRESS|print\(.*IndexProgressRenderer" && exit 1 || true
```

## Manual evidence
```bash
# update is no longer silent (stderr shows progress framing):
.venv/bin/python -c "import inspect; from java_codebase_rag.installer import run_update; src=inspect.getsource(run_update); assert 'quiet=True' not in src, 'still quiet'; print('ok')"
```

## Definition of Done
- [ ] All 7 PR-4 test names pass.
- [ ] Sentinels pass.
- [ ] `install`/`update` wizard stdout shape unchanged.
- [ ] Docs + README updated.
- [ ] PR title: `feat(cli): unified index progress for install/update; wire --quiet/--verbose`
- [ ] Branch: `feat/index-installer-progress`
````

---

## After all PRs land

- [ ] Move `propose/active/INDEX-OUTPUT-REWORK-PROPOSE.md` → `propose/completed/`.
- [ ] Move `plans/active/PLAN-INDEX-OUTPUT-REWORK.md` + this prompts file → `plans/completed/`.
- [ ] Confirm the whole-plan done definition in the plan is satisfied.
