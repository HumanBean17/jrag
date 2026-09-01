# jrag prime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four shipped skill/agent artifacts with `jrag prime` (a SessionStart-injected, navigation-framed orientation payload embedding the real `--help` surface), validated in the bench (#464 slice) before the breaking removal ships.

**Architecture:** Phase A adds the read-only `prime` subcommand (metadata-only state reads, direct markdown print, `--hook-json` envelope) and rewires bench condition D's tools section to runtime-generated prime output, then runs the gated slice. Phase B rewires the installer's CLI surface to write a SessionStart hook instead of deploying files, adds legacy-artifact cleanup to `update`, deletes the artifacts, and updates docs. Both phases land in one PR; the 0.13.0 tag waits for the gate.

**Tech Stack:** Python 3.11 argparse CLI (`src/java_codebase_rag/jrag.py`), pytest (`.venv/bin/python -m pytest`), bench harness (`bench/`), GitHub PR via `gh`.

**Spec:** `docs/superpowers/specs/active/2026-08-30-jrag-prime-design.md` (canonical payload template embedded there is the contract for Task 1).

## Global Constraints

- All Python via repo-root `.venv/bin/python` / `.venv/bin/pip`; editable install only.
- Before test runs: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}` (stale manual indexes hijack project-root discovery).
- Run only task-relevant tests during development; full suite once at the end (Task 11).
- On-disk `.java-codebase-rag*` names and `JAVA_CODEBASE_RAG_*` env vars are retained — do not rename.
- Lazy-import invariant of `src/java_codebase_rag/jrag.py` (documented `jrag.py:15-21`): no module-level import of torch / sentence-transformers / lancedb / pyarrow / cocoindex anywhere on the prime path.
- Bench discipline: preregister before running (Task 5 Step 1); never judge the system prompt itself.
- Commit after every green test cycle; work happens on branch `feat/jrag-prime` (already checked out).
- No method bodies or algorithms are specified here — implementers write code from the behavior contracts below.

---

### Task 1: `prime` payload template module (pure rendering)

**Files:**
- Create: `src/java_codebase_rag/prime.py`
- Test: `tests/package/test_prime_template.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Stdlib only.
- Produces (all later tasks rely on these exact names):
  - `PRIME_TEMPLATE: str` — module constant; the canonical template from the spec (identity paragraph ending "You are the explorer; jrag is the map.", trust-rule line, `**Index state**` bullets, `**Commands by group** (from \`jrag --help\`)` block with `http-routes`/`http-clients` normalized, `**Command reference**` with the command one-liners verbatim from `jrag --help` (the set tracks the live help; Task 1's drift guard enforces it), closing "Run `jrag <command> --help` for flags." line) with `{slot}` placeholders for computed values.
  - `@dataclass(frozen=True) class PrimeState` — fields: `freshness: str` (`"fresh"` | `"stale"`), `changed_files: int | None` (None = unknown/omitted), `last_increment_age: str` (pre-humanized, e.g. `"3h"`), `service_count: int`, `service_names: tuple[str, ...]` (already truncated), `symbol_count: int`, `route_count: int`, `client_count: int`, `producer_count: int`, `daemon_running: bool`.
  - `render(state: PrimeState) -> str` — fills slots; when `freshness == "stale"` and `changed_files` is not None the freshness line renders `stale — {changed_files} files changed since last increment`, else `fresh`; state bullets render exactly three lines per the spec template.
  - `render_hook_json(state: PrimeState) -> str` — `json.dumps` of `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": render(state)}}`, keys in that order, ensure_ascii=False.

- [ ] **Step 1: Write failing tests**

Five tests, all using a hand-built `PrimeState` (fresh, stale-with-56-changed, stale-with-None-changed):
1. `test_render_matches_canonical_shape` — render(fresh state) contains, in order: "You are the explorer; jrag is the map.", "**Trust rule:**", "**Index state**", "**Commands by group**", "**Command reference**", "Run `jrag <command> --help` for flags."; the state block renders exactly the three spec bullets with the state's numbers.
2. `test_render_stale_variants` — stale+56 → line contains "stale — 56 files changed since last increment"; stale+None → contains "stale" and no "files changed".
3. `test_hook_json_envelope` — `render_hook_json` output `json.loads`-parses; top key `hookSpecificOutput`; inner `hookEventName == "SessionStart"`; `additionalContext` equals `render(state)` for the same state.
4. `test_template_is_stdlib_pure` — `import java_codebase_rag.prime` in a subprocess with `-c`, assert process rc 0 and that importing the module does not pull heavy deps (check via a second `-c` that imports the module then prints sorted(sys.modules)); assert none of `torch`, `sentence_transformers`, `lancedb`, `pyarrow`, `cocoindex` present.
5. `test_template_tracks_real_help` — drift guard: run `.venv/bin/jrag --help` as subprocess; extract from its stdout every one-line command description (lines matching the `    <name>  <description>` positional block); assert each `-\`name`\` — <description>` line of the rendered payload appears in the real help output verbatim (modulo argparse line-wrapping: compare with internal whitespace collapsed).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_prime_template.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'java_codebase_rag.prime'`

- [ ] **Step 3: Write minimal implementation**

`prime.py` per the Produces contracts: template constant, frozen dataclass, `render` (str formatting only — no I/O, no imports beyond stdlib `dataclasses`/`json`), `render_hook_json`. The template text is copied from the spec's canonical template section verbatim.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_prime_template.py -v`
Expected: PASS (5/5)

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/prime.py tests/package/test_prime_template.py`
Run: `git commit -m "feat(prime): payload template module with hook-json envelope"`

---

### Task 2: `jrag prime` subcommand (state gathering, silence, latency guard)

**Files:**
- Modify: `src/java_codebase_rag/jrag.py` (subparser registration after the `status` block ~`jrag.py:556`; handler `_cmd_prime` near `_cmd_status` `jrag.py:1676`; dispatch registry if `cli_dispatch.py` lists verbs — check `cli_dispatch.py:134` area and add `"prime"` beside `"status"` if that registry enumerates read verbs)
- Test: `tests/package/test_jrag_prime.py`

**Interfaces:**
- Consumes: `prime.PrimeState`, `prime.render`, `prime.render_hook_json` (Task 1); existing `_resolve_cfg(args)` (`jrag.py:1329`), `_load_graph(cfg)` (`jrag.py:1355`), `_read_state_file(index_dir)` (`jrag.py:1643`), `watch.client.is_daemon_alive(index_dir)`; `_IndexNotFound` (`jrag.py:40`).
- Produces:
  - `_cmd_prime(args: argparse.Namespace) -> int` — behavior contract:
    - Resolve config via `_resolve_cfg`; attempt `_load_graph`. On `_IndexNotFound` or any resolution failure indicating no project/index: print nothing to stdout, return 0 (silence rule). On other unexpected exceptions: print nothing to stdout, one line to stderr, return 0 (hook-safe degradation).
    - Indexed path: read `graph.meta()` → `built_at` (epoch float), `counts` dict; `graph.microservice_counts()` → `dict[str, int]`. Derive: `symbol_count = counts["types"] + counts["members"]`, `route_count = counts["routes"]`, `client_count = counts["clients"]`, `producer_count = counts["producers"]` (missing counts keys degrade to 0, never raise).
    - `last_increment_age`: humanize `time.time() - built_at` — `<90m` → `"Xm"`, `<48h` → `"Xh"`, else `"Xd"` (integer, truncated).
    - `freshness`/`changed_files`: bounded walk `_staleness_since` (below); `"stale"` iff changed count > 0.
    - `service_names`: from `microservice_counts()` keys sorted by count descending, truncated to first 7 then `"…"` if more; `service_count` = full length.
    - `daemon_running = is_daemon_alive(index_dir)`; `_read_state_file` is the source for a future richer line but only liveness is rendered in v1.
    - Output: `print(render(state))`, or with `args.hook_json` → `print(render_hook_json(state))`. Return 0. Never emits the query Envelope, never calls `render()` from `jrag_render`.
  - `_staleness_since(built_at: float, source_root: Path, *, cap: int = 5000) -> int` in `prime.py` — walk `source_root` (os.walk), skip directories named `.git`, `target`, `build`, `node_modules`, `.java-codebase-rag`; count files with suffix `.java` or `.kt` whose `st_mtime > built_at`; stop and return `cap` when `cap` files found. Filesystem-metadata only.
  - Subparser: `prime = subparsers.add_parser("prime", help="Print agent priming context (index state + command surface).", parents=[_core_parser()])` — fresh parent parser per call, same as `status` (`jrag.py:543-555`); add `--hook-json` store_true flag (`help="Wrap output in the SessionStart hook JSON envelope."`); `prime.set_defaults(handler=_cmd_prime)`. Must NOT accept `--count`/`--exists`/`--fields` (it uses `_core_parser()`, not `_common_parser()`).

- [ ] **Step 1: Write failing tests**

Using session-scoped `corpus_root` / `ladybug_db_path` fixtures from `tests/conftest.py:128-158` and the subprocess helpers `_jrag_exe`/`_run_jrag` pattern from `tests/package/test_jrag_status.py:27-50` (env `JAVA_CODEBASE_RAG_SOURCE_ROOT=<corpus_root>`, `JAVA_CODEBASE_RAG_INDEX_DIR=<db parent>`):
1. `test_prime_prints_payload_on_indexed_repo` — rc 0; stdout contains "You are the explorer; jrag is the map.", "**Commands by group**", "`callers`", and a line matching `Index fresh`; state bullets present.
2. `test_prime_silent_without_index` — run in an empty `tmp_path` with env vars cleared/pointing at empty dir: rc 0, stdout exactly empty.
3. `test_prime_hook_json_envelope_valid` — `--hook-json`: rc 0; stdout parses as JSON with `hookSpecificOutput.hookEventName == "SessionStart"` and `additionalContext` containing the identity line.
4. `test_prime_reports_staleness` — after the fixture index build, `touch` one `.java` file in `corpus_root` with a newer mtime (`os.utime` future timestamp), re-run: stdout contains "stale" and "files changed".
5. `test_prime_import_guard` — in-process: import `java_codebase_rag.jrag`, build parser, invoke the prime handler with a Namespace for the indexed fixture; afterwards assert none of `torch`, `sentence_transformers`, `lancedb`, `pyarrow`, `cocoindex` in `sys.modules`.
6. `test_prime_rejects_query_output_flags` — `jrag prime --count` exits nonzero with argparse error (unknown-argument), mirroring how `_core_parser()` commands reject them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_jrag_prime.py -v`
Expected: FAIL — `prime: error: argument` / unknown subcommand `prime` (rc 2 from the CLI; import error for handler-level tests)

- [ ] **Step 3: Write minimal implementation**

`_cmd_prime` + `_staleness_since` + subparser registration per the Produces contracts. Reuse `_load_graph` (verified dependency-light). Keep all heavy imports (if ever needed) inside branches prime never takes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_jrag_prime.py tests/package/test_prime_template.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/jrag.py src/java_codebase_rag/prime.py tests/package/test_jrag_prime.py`
Run: `git commit -m "feat(prime): jrag prime subcommand — state-derived orientation, hook-json, silence rule"`

---

### Task 3: Bench — condition D tools section generated from real `jrag prime`

**Files:**
- Modify: `bench/conditions.yml` (D entry gains `tools: prime`)
- Modify: `bench/load_conditions.py` (`_CONDITION_KEYS` `:227-228`, validation in `_record_from_entry` `:249`, `to_flags` `:186-221`)
- Modify: `bench/claude_runner.py` (`run_cell` `:423-468` — pass prime context into `to_flags`)
- Test: `tests/bench/test_load_conditions.py` (update pinned invariants `:172-204`, add new tests)

**Interfaces:**
- Consumes: `_TOOLS_MARKER = "## Your tools"` split (`load_conditions.py:207-220`); `run_cell`'s per-cell env `JAVA_CODEBASE_RAG_INDEX_DIR` / `JAVA_CODEBASE_RAG_SOURCE_ROOT` (`claude_runner.py:466-468`); `jrag_bin` resolution (`claude_runner.py:153`, `run_bench.py:402-409`); Task 2's `jrag prime` CLI.
- Produces:
  - conditions.yml D entry field `tools: prime` (new optional key; absent = legacy file behavior). Loader validation: `tools` may only be `"prime"` and only on condition id `D` — anything else raises `ConfigError` with a message naming the condition.
  - `to_flags(cond, *, jrag_bin: Path | None = None, source_root: Path | None = None, index_dir: Path | None = None) -> ConditionFlags` — signature change (all new params keyword-only, default None). When `cond.tools == "prime"`: compose the tools section as `## Your tools\n\n` + generated payload, replacing whatever follows the marker in the prompt file's preamble/tools split; when any of the three context params is None in that case, raise `ConfigError("condition D requires jrag_bin/source_root/index_dir for prime generation")`. Legacy conditions behave exactly as today (file text verbatim).
  - `_generate_prime_tools_section(jrag_bin, source_root, index_dir) -> str` — module-level, memoized by the `(jrag_bin, source_root, index_dir)` triple (one subprocess per distinct index — a run spans multiple corpora, each with its own index, and each corpus's cells must see their own payload): runs `[str(jrag_bin), "prime"]` as subprocess with env `JAVA_CODEBASE_RAG_SOURCE_ROOT`/`JAVA_CODEBASE_RAG_INDEX_DIR` set, `check=True`, captures stdout; returns it. Subprocess failure raises `ConfigError` with stderr excerpt (bench must never silently fall back to a stale prompt).
  - `run_cell` passes to `to_flags` the same `jrag_bin` it received and the same source-root/index-dir values it sets as cell env (`claude_runner.py:466-468`) — the generated payload is byte-identical to what the agent's own `jrag` would print in-cell.
  - `JRAG_QUERY_VERBS` (`load_conditions.py:35-49`) is NOT extended — the agent never invokes `prime`; the shim stays as-is.
  - `prompt_hash` (`claude_runner.py:562-567`) stays a pure function of the composed prompt; memoization makes it stable across cells of one run.

- [ ] **Step 1: Write failing tests**

1. `test_d_tools_section_generated_from_prime` — monkeypatch `_generate_prime_tools_section` to return a fixed sentinel string; load conditions via `load_conditions()` fixture; call `to_flags(d_cond, jrag_bin=Path("/x/jrag"), source_root=Path("/sr"), index_dir=Path("/idx"))`; assert `append_system_prompt` = preamble (byte-identical to before the marker) + `## Your tools\n\n` + sentinel.
2. `test_prime_generation_memoized` — monkeypatch subprocess-calling helper; two `to_flags` calls for D → helper invoked exactly once; both payloads identical.
3. `test_prime_generation_missing_context_raises` — `to_flags(d_cond)` without context → `ConfigError` with "prime" in message.
4. `test_tools_prime_only_on_d` — a YAML with `tools: prime` on condition A → `ConfigError`.
5. Update pinned invariants (`:172-204`): with a monkeypatched sentinel payload, preamble remains byte-identical across A–D; tools sections pairwise distinct (B/C/A unchanged); D's tools section equals the sentinel; assertions that D contains "jrag" and "callers" now assert against a realistic sentinel containing both (use a snippet of the real template).
6. `test_run_cell_passes_prime_context` — existing `run_cell` test style: assert the composed system prompt contains the sentinel when the cell is condition D (monkeypatched generator).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/bench/test_load_conditions.py -v`
Expected: FAIL — `ConfigError: unknown key 'tools'` (loader rejects the new field today) / `TypeError` on `to_flags` kwargs

- [ ] **Step 3: Write minimal implementation**

Loader key + validation, memoized generator, `to_flags` signature change, `run_cell` wiring, conditions.yml D entry — per Produces contracts. `D_jrag_full.md` itself stays unchanged in this task (its tools section is simply replaced at load time; Step 1 of Task 5 records the baseline with the file-based behavior via a conditions file that omits `tools: prime`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/bench/ -v`
Expected: PASS (whole bench test dir)

- [ ] **Step 5: Commit**

Run: `git add bench/conditions.yml bench/load_conditions.py bench/claude_runner.py tests/bench/test_load_conditions.py`
Run: `git commit -m "feat(bench): condition D tools section generated from real jrag prime output"`

---

### Task 4: Bench — slice filter flags (`--only-conditions`, `--only-questions`)

**Files:**
- Modify: `bench/run_bench.py` (arg parsing `:342-361`, `expand_grid` call `:384`)
- Test: `tests/bench/test_run_bench_filters.py`

**Interfaces:**
- Consumes: `expand_grid(models, seeds, questions, conditions)` (`claude_runner.py:233` / `run_bench.py:384`), loaded `Condition` list and question list.
- Produces:
  - `--only-conditions` — comma-separated condition ids (e.g. `A,D`); validated: every id must exist in the loaded conditions, else `SystemExit` with message listing valid ids; filters the condition list before `expand_grid`. Default None = all.
  - `--only-questions` — comma-separated question ids; validated against loaded question ids the same way; filters the question list. Default None = all.
  - Both flags are pure pre-filtering — no changes to `run_grid`, `run_cell`, or conditions.yml invariants (the YAML still loads exactly `{A,B,C,D}`).

- [ ] **Step 1: Write failing tests**

1. `test_only_conditions_filters_grid` — with a loaded 4-condition fixture and `--only-conditions A,D`, the grid contains cells whose `cond` is only A or D.
2. `test_only_questions_filters_grid` — `--only-questions bc-sem-01,bc-tr-01` yields only those qids.
3. `test_unknown_condition_id_exits` — `--only-conditions A,E` → `SystemExit`; message contains "E" and the valid ids.
4. `test_no_flags_runs_all` — default behavior unchanged (cell count = len(questions) × len(conditions) × models × seeds).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/bench/test_run_bench_filters.py -v`
Expected: FAIL — unrecognized argument for both flags

- [ ] **Step 3: Write minimal implementation**

Two argparse flags + validation + filtering before `expand_grid`, per contracts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/bench/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

Run: `git add bench/run_bench.py tests/bench/test_run_bench_filters.py`
Run: `git commit -m "feat(bench): --only-conditions/--only-questions slice filters"`

---

### Task 5: Bench gate — preregistration, baseline + revised slices, cap-rate evaluation

**Files:**
- Modify: `bench/PREREGISTRATION.md` (new dated amendment section)
- Read-only artifacts: `bench/results/<run>/cells.jsonl` outputs; no source changes expected.

**Interfaces:**
- Consumes: Task 3 (prime-generated D), Task 4 (filters), existing corpora/indexes (`bench/checkouts`, `bench/indexes` — gitignored but present locally; if the bank-chat index is absent, rebuild via `.venv/bin/jrag init` + `reprocess` against `bench/checkouts/bank-chat`), report.py's cap counting (`report.py:310-316`), question categories from `bench/questions/bank-chat-system.jsonl`.
- Produces: two recorded runs + a gate verdict recorded in the PR body. Cap rate per condition × category = (cells with `exit_reason` in the cap/timeout set used by `report.py:310-316`) / (cells) — computed from `cells.jsonl` with the same exit-reason values report.py counts; categories joined from the questions file by qid.

- [ ] **Step 1: Preregister**

Append to `bench/PREREGISTRATION.md` an `## Amendment 2026-08-30 (prime payload gate)` section in the existing amendment style: claims under test (revised-D cap rate on call-trace + semantic ≤ A's on the slice; C1/C2 directionally improve; blast-radius not regressed), the exact commands and question set, and the decision rule (pass → Phase B ships; fail → artifacts stay, prime opt-in only). Commit.

- [ ] **Step 2: Baseline slice (old D prompt — before enabling `tools: prime`)**

Guard: verify `claude` CLI is authenticated and `glm-4.7` available; if not, record BLOCKED in the PR body and skip to Task 6 (the gate stays pending; release tagging waits).
Run the slice with `tools: prime` temporarily disabled for D (a local conditions copy without the key, passed via `--conditions`), command shape: `.venv/bin/python -m bench.run_bench --conditions <local-conditions-old-d.yml> --models glm-4.7 --seeds 0 --questions-glob bench/questions/bank-chat-system.jsonl --only-conditions A,D --max-turns 30 --wall-timeout 900`. Note the run dir.

- [ ] **Step 3: Revised slice (prime-generated D)**

Same command against `bench/conditions.yml` (D with `tools: prime`). Note the run dir. (Judge/grade/report are optional for the primary gate; run `.venv/bin/python -m bench.grade` + `report` per the module mains if credentials allow, for C1/C2 context.)

- [ ] **Step 4: Evaluate the gate**

Compute per-condition × category cap rates from both runs' `cells.jsonl`; compare: revised-D (call-trace + semantic) ≤ A's rate on the same slice; baseline-D vs revised-D delta recorded. Write the verdict (PASS/FAIL/BLOCKED + numbers) into the PR body draft (stored temporarily in `tmp/prime-gate-verdict.md`). Do not modify source based on the outcome inside this task — FAIL means the PR still ships Phase B code but the PR body must say the gate failed and 0.13.0 must not be tagged.

- [ ] **Step 5: Commit**

Run: `git add bench/PREREGISTRATION.md`
Run: `git commit -m "bench: preregister prime-payload gate (amendment 2026-08-30)"`

---

### Task 6: Installer — SessionStart hook merge/remove primitives

**Files:**
- Modify: `src/java_codebase_rag/installer.py` (new helpers near `merge_mcp_config` `:729`)
- Test: `tests/package/test_installer.py` (new test class beside `TestMergeMcpConfig` `:568`)

**Interfaces:**
- Consumes: `HostConfig` (`installer.py:68-97`) with `scope_path(scope, cwd)` (`:77`); the atomic-write pattern of `merge_mcp_config` (`:729-791`).
- Produces:
  - `hooks_settings_path(host: HostConfig, scope: Scope, cwd: Path) -> Path` — `host.scope_path(scope, cwd) / "settings.json"` for all three hosts (claude-code project `.claude/settings.json`, user `~/.claude/settings.json`; qwen/gigacode land in the same settings.json their MCP config already uses).
  - `merge_session_start_hook(config_path: Path, *, hook_command: str) -> bool` — reads JSON (missing file = `{}`; invalid JSON raises `ValueError`, same contract as `merge_mcp_config`); ensures `hooks` → `SessionStart` → list of matcher objects; within that list finds a matcher `{"matcher": "", "hooks": [...]}` and inside its `hooks` list any entry of `type == "command"` whose `command` contains `"jrag prime"` — replaces that entry in place, else appends `{"type": "command", "command": hook_command}`; creates the matcher object if absent. Never removes or reorders any other matcher, command entry, or sibling top-level key. Returns True iff a write happened (entry equality short-circuit → False, no write). Atomic write identical to `merge_mcp_config` (temp file in same dir, `json.dump` indent=2, fsync, `os.replace`).
  - `_remove_session_start_hook(config_path: Path, *, dry_run: bool = False) -> bool` — removes our command entry (same identification rule); drops the matcher object if its `hooks` list becomes empty; drops `SessionStart` if empty; drops `hooks` if empty; never touches anything else; missing file or absent entry → False (no-op success); respects `dry_run`.

- [ ] **Step 1: Write failing tests**

Mimic `TestMergeMcpConfig` style (`tests/package/test_installer.py:568-645`), tmp_path-based:
1. `test_hook_merge_into_empty_settings` — no file → creates `{"hooks": {"SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "<cmd>"}]}]}}`; returns True; second identical call returns False and file bytes unchanged (idempotent).
2. `test_hook_merge_preserves_siblings_and_other_hooks` — pre-seed `{"security": {...}, "$version": 5, "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}], "PreToolUse": [...]}}`; after merge all pre-existing content is intact and our entry was appended under `SessionStart` alongside `echo hi`.
3. `test_hook_merge_replaces_stale_command` — pre-seed our matcher with old command path `/old/jrag prime --hook-json`; merge with new command → exactly one `jrag prime` entry, bearing the new command.
4. `test_hook_merge_invalid_json_raises` — unparseable file → `ValueError`.
5. `test_hook_remove` — seeded file with our entry + others → remove drops only ours; empty containers pruned; second remove → False; `dry_run=True` → returns True but file unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_installer.py -k session_start_hook -v`
Expected: FAIL — `ImportError`/attribute not found

- [ ] **Step 3: Write minimal implementation**

The three helpers per contracts, reusing `merge_mcp_config`'s atomic-write mechanics.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_installer.py -v`
Expected: PASS (existing + new)

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/installer.py tests/package/test_installer.py`
Run: `git commit -m "feat(installer): SessionStart hook merge/remove primitives"`

---

### Task 7: Installer — manifest "hook" kind; CLI surface deploys a hook, not files

**Files:**
- Modify: `src/java_codebase_rag/installer.py` (`ARTIFACT_MANIFEST` `:140-150`; dispatchers `deploy_artifacts` `:802-856`, `refresh_artifacts` `:1417`, `_undeploy_surface` `:1726-1743`; `run_install` `:2074+` command resolution `:2167`)
- Modify: `src/java_codebase_rag/cli.py` (`install`/`update` `--surface` help text, `:988-997` / `:1038-1046`)
- Test: `tests/package/test_installer_surface.py` (update surface tests)

**Interfaces:**
- Consumes: Task 6 helpers; `_surface_binary(surface)` (`installer.py:717`); severity gate behavior (`run_install` `:2180-2201` — `.json` write failure = critical exit 1: intended, keep).
- Produces:
  - `ARTIFACT_MANIFEST["cli"] = [("hook", "", "")]` — single row, kind `"hook"` (no package file, no dest file); `"mcp"` surface becomes `[("mcp", "", "")]` — MCP entry only, no skill/agent rows.
  - Dispatcher behavior for kind `"hook"`: deploy/refresh → `hooks_settings_path` + `merge_session_start_hook(hook_command=...)` where `hook_command = f"{jrag_bin} prime --hook-json"` and `jrag_bin` comes from the same binary resolution the CLI surface already uses (`_surface_binary("cli")` / `resolve_mcp_command` path resolution — reuse, do not re-derive); undeploy → `_remove_session_start_hook(hooks_settings_path(...))`.
  - `--surface` help text (both commands) rewritten: `cli` = "jrag console-script + SessionStart prime hook (no files deployed)"; `mcp` = "stdio MCP server entry (tools only, no skill/agent artifacts)". Non-interactive default surface stays whatever `select_surface` (`:576`) currently defaults to — align the help text with the actual default (check `select_surface`; today's `install` help claims `mcp` default, `select_surface` may default `cli` — make help and behavior agree, tests pin the winner).
  - Install marker (`.java-codebase-rag.hosts`) — no shape change (surface `mcp|cli` already recorded).

- [ ] **Step 1: Write failing tests** (update/extend `tests/package/test_installer_surface.py`)

1. `test_cli_surface_deploys_hook_not_files` — `deploy_artifacts(..., surface="cli")` with monkeypatched `_read_package_artifact` sentinel: settings.json contains our SessionStart entry; no `skills/` or `agents/` files created anywhere under scope path.
2. `test_mcp_surface_deploys_entry_only` — surface `"mcp"`: MCP entry present; no skill/agent files deployed.
3. `test_refresh_updates_hook_command` — refresh with a changed jrag bin path → hook command updated in place, single entry.
4. `test_install_cli_end_to_end` — `run_install` non-interactive `--surface cli` (fake home via `monkeypatch.setattr(Path, "home", ...)`, `_stub_update_index_skip`-style stubbing per `:546`): marker written with `surface: "cli"`; hook present; exit 0.
5. `test_surface_help_matches_default` — parse `install --help` output; the `--surface` description names the same default `select_surface` returns non-interactively.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_installer_surface.py -v`
Expected: FAIL — cli surface still deploys skill/agent files (test 1), mcp still deploys pair (test 2)

- [ ] **Step 3: Write minimal implementation**

Manifest change + three dispatcher branches + help text, per contracts. Delete no files in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_installer_surface.py tests/package/test_installer.py -v`
Expected: PASS — existing migration tests (`test_update_migrates_mcp_to_cli` `:553`, `cli_to_mcp` `:609`, dry_run `:702`, user-scope `:874`) pass unmodified if the dispatchers are right; if a migration test asserts skill/agent file deployment, update it to assert hook/mcp-entry outcomes instead.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/installer.py src/java_codebase_rag/cli.py tests/package/test_installer_surface.py`
Run: `git commit -m "feat(installer): cli surface = SessionStart prime hook; mcp surface = tools only"`

---

### Task 8: Installer — legacy artifact cleanup on `update`

**Files:**
- Modify: `src/java_codebase_rag/installer.py` (new legacy list + removal function; call site in `run_update` `:1770+`, before refresh/migration)
- Test: `tests/package/test_installer_surface.py` or new `tests/package/test_installer_legacy_cleanup.py`

**Interfaces:**
- Consumes: `_remove_artifact_file(dest_path, *, dry_run)` (`installer.py:1699`, unlinks + best-effort parent rmdir).
- Produces:
  - `_LEGACY_ARTIFACT_PATHS: tuple[str, ...]` — the four 0.12.x deploy destinations: `"skills/explore-codebase/SKILL.md"`, `"agents/explorer-rag-enhanced.md"`, `"skills/explore-codebase-cli/SKILL.md"`, `"agents/explorer-rag-cli.md"` (relative to the surface's skills/agents dir roots — i.e. resolve against `host.skills_dir(scope, cwd)` / `host.agents_dir(scope, cwd)` exactly as `deploy_artifacts` resolves manifest rows).
  - `_remove_legacy_artifacts(hosts, scope, cwd, *, dry_run: bool = False) -> list[str]` — removes every existing legacy file across the given hosts/scope (both pairs — a user may have switched surfaces across versions; the marker records only the current one); returns the list of removed paths (empty list = nothing to do, still a success); `dry_run=True` returns what would be removed without writing.
  - `run_update` calls it unconditionally (both surfaces, migration and refresh branches) after host detection, printing one summary line (`removed N legacy skill/agent file(s)` style, silent when N=0).

- [ ] **Step 1: Write failing tests**

1. `test_update_removes_012_artifacts` — fixture mimicking a 0.12.x deployment: all four files planted at their dest paths (plus a user file `skills/explore-codebase-cli/NOTES.txt` to prove sibling preservation); `run_update` (surface unchanged, index step stubbed) → all four gone; `NOTES.txt` and its directory survive; hook/mcp entry per current surface intact.
2. `test_legacy_cleanup_dry_run` — same fixture, `--dry-run` → files still present; report lists the four paths.
3. `test_legacy_cleanup_idempotent` — second `run_update` → no error, no output noise.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_installer_legacy_cleanup.py -v`
Expected: FAIL — legacy files survive update

- [ ] **Step 3: Write minimal implementation**

List + function + call site per contracts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_installer_surface.py tests/package/test_installer_legacy_cleanup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/installer.py tests/package/test_installer_legacy_cleanup.py`
Run: `git commit -m "feat(installer): update removes legacy 0.12.x skill/agent deployments"`

---

### Task 9: Artifact deletion + packaging sync

**Files:**
- Delete: `skills/explore-codebase/SKILL.md`, `skills/explore-codebase-cli/SKILL.md`, `skills/README.md`, `agents/explorer-rag-enhanced.md`, `agents/explorer-rag-cli.md` (and now-empty dirs)
- Delete: the mirrored copies under `src/java_codebase_rag/install_data/skills/`, `src/java_codebase_rag/install_data/agents/`
- Modify: `scripts/sync_agent_artifacts.py` (`PAIRS` `:30-32` — drop removed pairs)
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]` `:85-86` — drop the two install_data globs)
- Test: `tests/package/test_install_data_sync.py` (existing — must pass unmodified)

**Interfaces:**
- Consumes: Task 7 (manifest no longer references the files), Task 8 (legacy cleanup covers deployed copies).
- Produces: a tree with no `skills/` or `agents/` consumer artifacts; `install_data/` retaining only what other features ship (if anything — verify with `ls`; if the dirs become empty, remove them and their package-data entries entirely); `python -m scripts.sync_agent_artifacts --check` green.

- [ ] **Step 1: Verify the sync check fails first (red)**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts} && .venv/bin/python -m pytest tests/package/test_install_data_sync.py -v`
Expected: after deleting the repo files in Step 2 the check flags orphaned install_data copies — do Step 2's deletions first, then this check must FAIL on the leftover mirrors (this ordering proves the check works); then finish Step 2 and it passes.

- [ ] **Step 2: Delete repo copies, install_data mirrors, PAIRS entries, pyproject globs**

Per the file list. `git rm` for tracked deletions.

- [ ] **Step 3: Run sync + package tests**

Run: `.venv/bin/python scripts/sync_agent_artifacts.py --check && .venv/bin/python -m pytest tests/package/test_install_data_sync.py tests/package/test_installer_surface.py -v`
Expected: PASS

- [ ] **Step 4: Sanity: installer still green**

Run: `.venv/bin/python -m pytest tests/package/test_installer.py -v`
Expected: PASS (no test references the deleted package files after Tasks 7-8 updated the surface tests)

- [ ] **Step 5: Commit**

Run: `git add -A skills agents src/java_codebase_rag/install_data scripts/sync_agent_artifacts.py pyproject.toml`
Run: `git commit -m "feat!: remove skill/agent consumer artifacts; CLI surface ships the prime hook"`

---

### Task 10: Documentation updates

**Files:**
- Modify: `docs/JRAG-CLI.md` (prime command page: payload states, `--hook-json`, silence rule, manual hook JSON for unsupported hosts; install flows: cli = hook, mcp = entry; exit codes: prime always 0)
- Modify: `docs/AGENT-GUIDE.md` (reposition: human reference for the MCP surface and hook-less hosts; remove the "copy-paste into AGENTS.md/CLAUDE.md" mandate; point to `jrag prime` as the injection mechanism)
- Modify: `docs/DESIGN.md`, `docs/ARCHITECTURE.md` (surfaces sections: skill/agent artifacts → prime + SessionStart hook; write path gains hook deploy/refresh/teardown)
- Modify: `CLAUDE.md` ("Shipped artifacts" section: skills/agents gone; prime template is source; hook deployed by install)
- Modify: `README.md` (claims referencing skills/subagents)
- Modify: `docs/superpowers/specs/active/2026-08-30-jrag-prime-design.md` (one factual correction, present-tense: the install marker records hook presence via the existing `surface` field — no new record shape)

**Interfaces:**
- Consumes: everything shipped by Tasks 1-9.
- Produces: docs consistent with the shipped behavior; no doc claims files that no longer exist.

- [ ] **Step 1: Update each doc per the file list**

Content contracts: JRAG-CLI prime section must show the payload template, the three silence states, the exact manual hook JSON (matcher/hook/command shape from Task 6), and note that operator commands are excluded from prime output. AGENT-GUIDE keeps MCP tool reference value but its intro stops instructing paste-in. Global rule from repo CLAUDE.md: docs are either operator-facing or the two internal ones — keep that split.

- [ ] **Step 2: Verify no stale references**

Run: `grep -rn "explore-codebase\|explorer-rag" README.md docs/ CLAUDE.md --include="*.md" | grep -v superpowers`
Expected: only historical/archive mentions (MIGRATION.md rename map if any, archive dirs) — none prescribing current use.

- [ ] **Step 3: Commit**

Run: `git add docs CLAUDE.md README.md`
Run: `git commit -m "docs: prime + SessionStart hook replace skill/agent artifacts"`

---

### Task 11: Full suite + branch finalization

**Files:**
- None created; verification only.

**Interfaces:**
- Consumes: all tasks.
- Produces: green full suite on `feat/jrag-prime`; clean working tree.

- [ ] **Step 1: Clean stale indexes and run the full suite**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts} && .venv/bin/python -m pytest`
Expected: PASS (0 failures). If editable-install staleness is suspected: `.venv/bin/pip install -e ".[dev]"` once, re-run — do not report it.

- [ ] **Step 2: Verify CLI surface end-to-end**

Run: `.venv/bin/jrag prime` in repo root (expect: silence — this repo has no index) and in an indexed fixture dir if available (expect: full payload). Run `.venv/bin/jrag --help` and confirm `prime` appears grouped with health/read commands.

- [ ] **Step 3: Commit any leftovers; confirm clean tree**

Run: `git status --porcelain`
Expected: empty (except untracked `tmp/` artifacts, which stay untracked)

- [ ] **Step 4: Hand off to PR + review**

Push `feat/jrag-prime`; open PR to `master` (PR body: summary, gate verdict from Task 5 with numbers or BLOCKED, breaking-change note for 0.13.0, dual-PyPI reminder); then invoke the `requesting-code-review` skill.

---

## Self-Review (run after writing; fixes applied inline)

1. **Code scan:** no method bodies or algorithms; behavior contracts, signatures, data shapes, and expected test outcomes only. ✓
2. **Self-containment:** every task's Interfaces block carries full contracts (names, signatures, shapes, error cases); no "see spec" except the canonical template, which Task 1 copies verbatim from the spec's embedded template section. ✓
3. **Spec coverage:** payload contract → T1/T2; silence/envelope/latency → T2; CLI-only + MCP-tools-only surfaces → T7; wizard hook wiring → T6/T7; update cleanup of all four artifacts → T8; artifact/packaging removal → T9; bench revision + gate → T3/T4/T5; docs incl. AGENT-GUIDE repositioning → T10; testing (unit + full suite) → per-task + T11; rollout gate recorded in PR → T5/T11. Marker correction → T10. ✓
4. **Placeholder scan:** no TBD/TODO/vague-error-handling; every test step names scenario + expected result. ✓
5. **Type consistency:** `PrimeState`/`render`/`render_hook_json` (T1) consumed by T2 unchanged; `to_flags` signature (T3) matches `run_cell` wiring; `_remove_legacy_artifacts` (T8) name used consistently. ✓
