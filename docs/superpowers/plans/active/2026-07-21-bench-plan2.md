# Benchmark Agent Harness Driver Implementation Plan (Plan 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `run_bench.py` driver that runs the benchmark grid one headless `claude -p` cell at a time and emits one JSONL line per cell — the evidence `grade.py` (Plan 2b) consumes — and prove it on a 12-cell smoke grid.

**Architecture:** A condition-agnostic driver. `bench/run_bench.py` expands the grid from `(questions × conditions × models × seeds)` and dispatches each cell through `bench/claude_runner.py`, which assembles the `claude -p` argv from Plan 1's loaders, spawns it with the four de-risk corrections (driver-side turn cap, `--verbose` + `stdin=DEVNULL`, subprocess `cwd`=checkout, enforcement monitored via `tool_call_breakdown`), single-pass-parses stream-json, and returns a `CellResult`. Grading, ablations, and the full run are out of scope.

**Tech Stack:** Python 3 (`.venv/bin/python`), pytest, PyYAML, the `claude` CLI headless (`-p`), the `java-codebase-rag` operator CLI for index builds (already done in Plan 1).

## Global Constraints

- Use `.venv/bin/python` and `.venv/bin/pip` only — never system `python`/`pip`. Editable install of `java-codebase-rag` is enforced by `tests/conftest.py`.
- `bench/` is a standalone package imported as `import bench.<module>`; never fold it into `java_codebase_rag`. `tests/bench/` deliberately has NO `__init__.py` (see `tests/bench/conftest.py` — re-adding it shadows the source package).
- Run only the relevant pytest subset during development; run the full `tests/bench/` suite once at the end.
- Bench indexes/checkouts live under `bench/indexes/` and `bench/checkouts/` (gitignored, built in Plan 1). Plan 2a adds `bench/results/` (also gitignored).
- Always pass jrag paths as **absolute** (`--index-dir` resolves relative to `--source-root` — see `bench/PHASE0_FINDINGS.md`). The MCP `command` must be the absolute venv python (`sys.executable`), not bare `python`.
- `claude -p` requires `--verbose` with `--output-format stream-json`, and its stdin must be closed (`stdin=DEVNULL`). There is no `--max-turns` flag — the turn cap is enforced driver-side. There are no temperature/seed flags — `seed`/`temperature` are recorded as intended metadata only.
- Every task commits with `Co-Authored-By: Claude <noreply@anthropic.com>` at the end of the message.

---

## File Structure (Plan 2a deliverables)

```
bench/
  claude_runner.py              # CellSpec -> argv, spawn claude -p, turn cap, parse stream -> CellResult
  run_bench.py                  # grid expansion + dispatch + results write (idempotent, resumable) + CLI
  conditions.yml                # condition C `name` relabeled (amendment) — MODIFY
  PREREGISTRATION.md            # condition-C amendment appended (dated) — MODIFY
  results/                      # run outputs — NEW, gitignored
tests/bench/
  test_claude_runner.py         # stream parse, mcp materialize, argv assembly, run_cell + cap
  test_run_bench.py             # grid expansion, results write, idempotency/resume, CLI orchestration
  fixtures/streams/             # canned stream-json samples (incl. the real run-4 transcript)
  fixtures/fake_claude/         # tiny scripts that emit canned stream-json for run_cell tests
```

**Responsibility split.** `claude_runner.py` owns everything about one cell: argv assembly, MCP config materialization, subprocess spawn + turn cap, stream parsing, and the `CellResult`/JSONL schema. `run_bench.py` owns the grid (expansion, dispatch, results I/O, resume, CLI) and depends only on `claude_runner`'s public surface (`CellSpec`, `run_cell`, `CellResult`). Neither module knows about grading.

---

### Task 1: Stream-json parser (pure)

**Files:**
- Create: `bench/claude_runner.py`
- Create: `tests/bench/test_claude_runner.py`
- Create: `tests/bench/fixtures/streams/minimal_done.jsonl` (hand-authored, ~6 lines)
- Reference: `bench/spikes/run4-condition-D-bc-impl-01.stream.jsonl` (real transcript — copy into fixtures)

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `@dataclass(frozen=True) class StreamSummary` with fields: `tool_call_breakdown: dict[str,int]` (tool_use name → count), `context_bytes_retrieved: int` (sum of tool_result content lengths), `n_turns: int` (count of `assistant` stream events), `tokens: dict` (`{"input": int, "output": int, "total": int}`; `total` = input+output), `stop_reason: str | None`, `terminal_reason: str | None`, `is_error: bool`, `api_error_status: str | None`, `final_answer: str | None` (the `result.result` field), `num_turns_reported: int | None` (the `result.num_turns` field).
  - `parse_stream(lines: Iterable[str]) -> StreamSummary` — single-pass over stream-json lines. Behavior:
    - Skip non-JSON / blank lines silently.
    - For an event with `type == "assistant"`: increment `n_turns`; for each `message.content[]` entry of `type == "tool_use"`, increment `tool_call_breakdown[name]`; (text blocks are ignored by the summary).
    - For an event with `type == "user"`: for each `message.content[]` entry of `type == "tool_result"`, add the length (in characters) of its `content` (stringified) to `context_bytes_retrieved`.
    - For an event with `type == "result"`: populate `stop_reason`, `terminal_reason`, `is_error`, `api_error_status`, `final_answer` (= `result`), `num_turns_reported` (= `num_turns`), and `tokens` from `usage` (`input_tokens`, `output_tokens`; `total` computed). If no `result` event is present, the corresponding fields stay `None`/defaults (this is the capped/truncated case).
    - Events with `type == "system"` are ignored.

- [ ] **Step 1: Write the failing tests**

  `test_parse_minimal_done` — given `minimal_done.jsonl` containing exactly: one `system/init` event, one `assistant` event whose content has a `tool_use` of name `Read` and a text block, one `user` event with a `tool_result` whose content is the 6-char string `"hello\n"`, and one `result` event with `num_turns=1`, `stop_reason="end_turn"`, `terminal_reason="completed"`, `is_error=false`, `usage={"input_tokens":100,"output_tokens":5}` — `parse_stream` returns a `StreamSummary` with `n_turns==1`, `tool_call_breakdown=={"Read":1}`, `context_bytes_retrieved==6`, `tokens=={"input":100,"output":5,"total":105}`, `stop_reason=="end_turn"`, `terminal_reason=="completed"`, `is_error is False`, `num_turns_reported==1`.

  `test_parse_real_run4` — copy `bench/spikes/run4-condition-D-bc-impl-01.stream.jsonl` to `tests/bench/fixtures/streams/run4.jsonl`. `parse_stream` over it returns a summary with `tool_call_breakdown == {"mcp__jrag__resolve":1, "mcp__jrag__neighbors":1}`, `n_turns >= 2`, `num_turns_reported == 3`, `terminal_reason == "completed"`, `is_error is False`, and `final_answer` containing the substring `"AckProcessor"`.

  `test_parse_truncated_no_result` — a stream with one `assistant` tool_use event and NO `result` event: `n_turns==1`, `tool_call_breakdown` populated, `terminal_reason is None`, `num_turns_reported is None`.

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: FAIL — `ModuleNotFoundError: bench.claude_runner`.

- [ ] **Step 3: Write minimal implementation**

  Create `bench/claude_runner.py` defining `StreamSummary` and `parse_stream` per the Produces contract above. Use `json.loads` per line in a try/except that skips non-JSON lines. (No subprocess, no I/O beyond the passed iterator.)

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/claude_runner.py tests/bench/test_claude_runner.py tests/bench/fixtures/streams/`
  Run: `git commit -m "feat(bench): stream-json parser -> StreamSummary"`

---

### Task 2: MCP config materialization (pure)

**Files:**
- Modify: `bench/claude_runner.py`
- Modify: `tests/bench/test_claude_runner.py`

**Interfaces:**
- Consumes: `bench/mcp/jrag.json` template (reads at runtime via a path argument).
- Produces:
  - `materialize_mcp_config(template_path: str, index_dir_abs: str, source_root_abs: str, venv_python: str, dest_path: str) -> str` — reads the template JSON, substitutes the literal substrings `${JRAG_INDEX_DIR}` → `index_dir_abs` and `${JRAG_SOURCE_ROOT}` → `source_root_abs` everywhere in the serialized JSON, rewrites the `mcpServers.jrag.command` value to `venv_python` (the template ships `"python"`, which does not resolve when `claude` spawns the server), writes the result to `dest_path`, and returns `dest_path`. Raises `ConfigError` (a `claude_runner` exception type) if the template has no `mcpServers.jrag` key or no `env.JAVA_CODEBASE_RAG_INDEX_DIR` placeholder.

- [ ] **Step 1: Write the failing test**

  `test_materialize_substitutes_and_rewrites_command` — call `materialize_mcp_config` with a temp copy of a template containing `{"mcpServers":{"jrag":{"command":"python","args":["-m","java_codebase_rag.mcp.server"],"env":{"JAVA_CODEBASE_RAG_INDEX_DIR":"${JRAG_INDEX_DIR}","JAVA_CODEBASE_RAG_SOURCE_ROOT":"${JRAG_SOURCE_ROOT}"}}}}`, `index_dir_abs="/x/idx"`, `source_root_abs="/y/src"`, `venv_python="/z/bin/python"`, `dest_path=<tmp file>`. Reload the written file as JSON and assert: `mcpServers.jrag.env.JAVA_CODEBASE_RAG_INDEX_DIR == "/x/idx"`, `...SOURCE_ROOT == "/y/src"`, `mcpServers.jrag.command == "/z/bin/python"`, and the return value equals `dest_path`.

  `test_materialize_rejects_template_without_jrag` — a template whose top key is `mcpServers` but has no `jrag` entry → raises `ConfigError` whose message names the missing `jrag` server.

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: FAIL — `materialize_mcp_config` not defined.

- [ ] **Step 3: Write minimal implementation**

  Add `ConfigError` and `materialize_mcp_config` to `bench/claude_runner.py` per the Produces contract. Substitute on the serialized string (handles the placeholder wherever it appears), then `json.loads` → rewrite `command` → write out.

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/claude_runner.py tests/bench/test_claude_runner.py`
  Run: `git commit -m "feat(bench): per-cell MCP config materialization"`

---

### Task 3: CellSpec + argv assembly (pure)

**Files:**
- Modify: `bench/claude_runner.py`
- Modify: `tests/bench/test_claude_runner.py`

**Interfaces:**
- Consumes:
  - `bench.load_conditions.Condition`, `ConditionFlags`, `to_flags`, constants `ALL_JRAG_TOOLS`.
  - `bench.load_corpora.CorpusRecord` (needs `checkout_path`, `index.index_dir`, `commit_sha`/`pinned_repo_sha`, `name`).
  - `bench.load_questions.Question` (needs `question` text).
  - `materialize_mcp_config` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class CellSpec` with fields: `question: Question`, `condition: Condition`, `corpus: CorpusRecord`, `model: str`, `seed: int`, `temperature: float`, `max_turns: int`, `repo_root: str`.
  - `build_argv(spec: CellSpec, flags: ConditionFlags, mcp_config_path: str | None) -> list[str]` — returns the exact `claude` argv. The list always begins with the literal `"claude"` and contains, in order: `"-p"`, the question text (`spec.question.question`), `"--output-format","stream-json"`, `"--verbose"`, `"--permission-mode","bypassPermissions"`, `"--model", spec.model`, `"--add-dir", <absolute checkout>`, `"--append-system-prompt", flags.append_system_prompt` (the prompt CONTENTS string, passed inline), `"--allowedTools", <comma-joined flags.allowed_tools>`. If `flags.disallowed_tools` is non-empty, append `"--disallowedTools", <comma-joined>`. If `mcp_config_path is not None`, append `"--mcp-config", mcp_config_path, "--strict-mcp-config"`. The absolute checkout is `os.path.join(spec.repo_root, spec.corpus.checkout_path)`.
  - `cell_cwd(spec: CellSpec) -> str` — returns the absolute checkout path (same as the `--add-dir` target).
  - `run_id(spec: CellSpec) -> str` — `f"{spec.question.id}_{spec.condition.id}_{spec.model}_s{spec.seed}"`.

- [ ] **Step 1: Write the failing tests**

  `test_argv_condition_A_no_mcp` — a `CellSpec` with condition A (`allowed_tools=["Grep","Glob","Read","Bash"]`, `mcp_servers=[]`) and a `ConditionFlags` with `mcp_config_arg=None`; `build_argv(spec, flags, mcp_config_path=None)` returns a list that: starts with `"claude"`, contains `"--output-format","stream-json"`,`"--verbose"`,`"--permission-mode","bypassPermissions"`,`"--model",<model>`,`"--add-dir",<abs checkout>`,`"--append-system-prompt",<prompt contents>`,`"--allowedTools","Grep,Glob,Read,Bash"`, does NOT contain `"--mcp-config"` or `"--strict-mcp-config"`, and does NOT contain the substring `"--max-turns"` anywhere.

  `test_argv_condition_D_with_mcp` — condition D (`allowed_tools` includes all `ALL_JRAG_TOOLS` + Read/Grep/Glob, `mcp_servers=["jrag"]`); `build_argv(spec, flags, mcp_config_path="/tmp/x.json")` contains `"--mcp-config","/tmp/x.json","--strict-mcp-config"` and `"--allowedTools"` whose value contains every member of `ALL_JRAG_TOOLS`.

  `test_argv_condition_B_denies_graph` — condition B; the `--disallowedTools` value equals the comma-joined `["mcp__jrag__find","mcp__jrag__describe","mcp__jrag__neighbors","mcp__jrag__resolve"]` (graph tools) and the `--allowedTools` value contains `mcp__jrag__search`.

  `test_run_id_format` — `run_id(spec)` for question `bc-impl-01`, condition `D`, model `glm-4.7`, seed `0` returns `"bc-impl-01_D_glm-4.7_s0"`.

  `test_argv_records_no_temperature_flag` — assert no element of `build_argv(...)` equals `"--temperature"` or `"--seed"` (they do not exist as flags; the spec records intended values in JSONL only).

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: FAIL — `CellSpec`/`build_argv` not defined.

- [ ] **Step 3: Write minimal implementation**

  Add `CellSpec`, `build_argv`, `cell_cwd`, `run_id` to `bench/claude_runner.py` per the Produces contract. Join tool lists with `","`. Resolve the absolute checkout against `spec.repo_root`.

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/claude_runner.py tests/bench/test_claude_runner.py`
  Run: `git commit -m "feat(bench): CellSpec + claude -p argv assembly"`

---

### Task 4: CellResult + JSONL schema (pure)

**Files:**
- Modify: `bench/claude_runner.py`
- Modify: `tests/bench/test_claude_runner.py`

**Interfaces:**
- Consumes: `StreamSummary` (Task 1), `CellSpec` (Task 3), `run_id` (Task 3).
- Produces:
  - `@dataclass(frozen=True) class CellResult` with the JSONL schema fields: `run_id: str`, `question_id: str`, `corpus: str`, `corpus_commit: str`, `condition: str`, `model: str`, `seed: int`, `temperature: float`, `claude_code_version: str | None`, `ontology_version: int`, `index_build_id: str | None`, `prompt_hash: str`, `started_at: str`, `finished_at: str`, `wall_s: float`, `n_turns: int`, `n_tool_calls: int`, `tool_call_breakdown: dict[str,int]`, `tokens: dict`, `context_bytes_retrieved: int`, `exit_reason: str`, `final_answer: str | None`, `transcript_path: str`, `grade: None`. (`grade` is always `None` in Plan 2a — Plan 2b fills it.)
  - `to_cell_jsonl(result: CellResult) -> dict` — returns a dict keyed exactly by the field names above (snake_case), JSON-serializable, with `grade` present and `None`. Used to write `cell.jsonl` and the aggregated `cells.jsonl`.
  - `exit_reason` derivation rule (the caller that builds `CellResult` applies it): if the driver capped the run → `"cap"`; elif `summary.is_error` or `summary.api_error_status` → `"error"`; else `"done"`.
  - `n_turns` selection rule: `summary.num_turns_reported` if not None, else `summary.n_turns` (the counted value, used when the run was capped before a `result` event).

- [ ] **Step 1: Write the failing tests**

  `test_to_cell_jsonl_has_schema_keys` — construct a `CellResult` with representative values; `to_cell_jsonl(result)` is a dict whose key set equals exactly the 23 schema field names listed above; `result.grade is None`; the dict round-trips through `json.dumps`/`json.loads`.

  `test_exit_reason_done` — given a `StreamSummary` with `is_error=False`, `api_error_status=None` and `capped=False`, the derived `exit_reason` is `"done"`.

  `test_exit_reason_cap_overrides` — `capped=True` (regardless of summary fields) → `exit_reason == "cap"`.

  `test_exit_reason_error` — `is_error=True`, `capped=False` → `exit_reason == "error"`.

  `test_n_turns_prefers_reported` — `summary.num_turns_reported=3`, `summary.n_turns=2` → chosen `n_turns == 3`; when `num_turns_reported is None`, `n_turns == summary.n_turns`.

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: FAIL — `CellResult`/`to_cell_jsonl` not defined.

- [ ] **Step 3: Write minimal implementation**

  Add `CellResult`, `to_cell_jsonl`, plus module-level helpers `derive_exit_reason(summary, capped) -> str` and `choose_n_turns(summary) -> int` implementing the rules above. Keep them pure (no I/O).

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/claude_runner.py tests/bench/test_claude_runner.py`
  Run: `git commit -m "feat(bench): CellResult JSONL schema + exit_reason/n_turns rules"`

---

### Task 5: run_cell — subprocess spawn + driver-side turn cap (integration)

**Files:**
- Modify: `bench/claude_runner.py`
- Modify: `tests/bench/conftest.py` (add `requires_claude` marker + skip gate)
- Modify: `tests/bench/test_claude_runner.py`
- Create: `tests/bench/fixtures/fake_claude/emit_short.sh` (emits 1 assistant event + result; no cap)
- Create: `tests/bench/fixtures/fake_claude/emit_long.sh` (emits many assistant events; triggers cap)

**Interfaces:**
- Consumes: `build_argv`, `cell_cwd`, `materialize_mcp_config`, `to_flags` (from `load_conditions`), `parse_stream`, `CellResult`, `derive_exit_reason`, `choose_n_turns`, `StreamSummary` (Tasks 1-4).
- Produces:
  - `run_cell(spec: CellSpec, *, claude_bin: str = "claude", jrag_mcp_template: str = "bench/mcp/jrag.json", results_transcript_path: str, venv_python: str | None = None) -> CellResult` — behavior:
    - Compute `flags = to_flags(spec.condition)`.
    - If `flags.mcp_config_arg is not None`: call `materialize_mcp_config(jrag_mcp_template, abs index dir, abs checkout, venv_python or sys.executable, <tmp path>)` to get `mcp_config_path`; else `mcp_config_path = None`.
    - `argv = build_argv(spec, flags, mcp_config_path)`.
    - Spawn `claude_bin` with `argv`, `cwd=cell_cwd(spec)`, `stdin=DEVNULL`, `stdout=PIPE`, streaming stdout line by line. Append every raw line to a transcript buffer and write it to `results_transcript_path`.
    - Count `assistant` events as they stream; if the count would exceed `spec.max_turns`, SIGTERM the process, stop reading, set `capped=True`.
    - After the process exits, `summary = parse_stream(buffer)`.
    - Capture `claude_code_version` from the spawned binary via `claude_bin --version` (best-effort; `None` on failure).
    - Compute timing (`started_at`, `finished_at`, `wall_s`), `prompt_hash` (sha256 of `flags.append_system_prompt`, hex, prefixed `sha256:`), `corpus_commit` (= `spec.corpus.commit_sha or spec.corpus.pinned_repo_sha`), `ontology_version` (= `spec.corpus.index.ontology_version`), `index_build_id` (= `spec.corpus.index.build_id`).
    - Build and return a `CellResult` with `exit_reason = derive_exit_reason(summary, capped)`, `n_turns = choose_n_turns(summary)`, `n_tool_calls = sum(summary.tool_call_breakdown.values())`, `transcript_path = results_transcript_path`, `grade = None`.
  - `ConfigError` is reused for materialization failures.

- [ ] **Step 1: Add the `requires_claude` marker to conftest**

  In `tests/bench/conftest.py`: register a `requires_claude` ini marker line in `pytest_configure`, and in `pytest_collection_modifyitems` add a skip marker (`shutil.which("claude") is None`) to any item with the `requires_claude` keyword. Mirror the existing `requires_jqa`/`requires_jdk` pattern exactly.

- [ ] **Step 2: Write the failing tests**

  `test_run_cell_caps_at_max_turns` — a `CellSpec` with `max_turns=2`; monkeypatch nothing but pass `claude_bin=<path to emit_long.sh>` (a shell script that prints N>3 valid stream-json `assistant` events then a `result` event, each on its own line). `run_cell(...)` returns a `CellResult` with `exit_reason == "cap"` and `n_turns` reflecting the count up to the cap. Assert the script was terminated early (the result event was NOT reached — e.g. the transcript file does not contain `"type":"result"`).

  `test_run_cell_completes_no_cap` — `claude_bin=<path to emit_short.sh>` (prints one `assistant` event with a `tool_use` of name `Read`, then a `result` event with `num_turns=1`, `terminal_reason="completed"`, `usage` tokens). `run_cell(...)` returns `exit_reason == "done"`, `n_turns == 1`, `tool_call_breakdown == {"Read":1}`, `grade is None`, and the transcript file written to `results_transcript_path` contains the raw lines.

  `test_run_cell_no_mcp_for_condition_A` — a condition-A spec: `run_cell` must NOT create any `--mcp-config` temp file and the spawned argv contains no `--mcp-config`. (Assert by intercepting: have `emit_short.sh` record its argv to a sidecar file, or monkeypatch `materialize_mcp_config` to fail-if-called.)

  Mark all three `@pytest.mark.requires_claude` is NOT needed here (they use a fake binary), but the fake scripts must be executable (`chmod +x`).

- [ ] **Step 3: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: FAIL — `run_cell` not defined.

- [ ] **Step 4: Write minimal implementation**

  Add `run_cell` to `bench/claude_runner.py` per the Produces contract. Use `subprocess.Popen` with `bufio` line iteration over `stdout`, `signal.SIGTERM` to cap, and `time.time()` for timing. Write the transcript incrementally.

- [ ] **Step 5: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_claude_runner.py -v`
  Expected: PASS.

- [ ] **Step 6: Commit**

  Run: `git add bench/claude_runner.py tests/bench/conftest.py tests/bench/test_claude_runner.py tests/bench/fixtures/fake_claude/`
  Run: `git commit -m "feat(bench): run_cell — spawn claude -p, driver-side turn cap, -> CellResult"`

---

### Task 6: Grid expansion (pure, in run_bench)

**Files:**
- Create: `bench/run_bench.py`
- Create: `tests/bench/test_run_bench.py`

**Interfaces:**
- Consumes: `bench.load_questions.load_all_questions` / `Question`, `bench.load_conditions.load_conditions` / `Condition`, `bench.load_corpora.load_corpora` / `CorpusRecord`, `bench.claude_runner.CellSpec`.
- Produces:
  - `expand_grid(questions: list[Question], conditions: list[Condition], corpora: list[CorpusRecord], models: list[str], seeds: list[int], temperature: float, max_turns: int, repo_root: str) -> list[CellSpec]` — the cross-product `(questions × conditions × models × seeds)`, where each `Question` is paired with the `CorpusRecord` whose `name == question.corpus`. Order: questions in input order, then conditions A→D, then models, then seeds. Raises `ConfigError` (a `run_bench` exception type) if any question's `corpus` has no matching `CorpusRecord`.

- [ ] **Step 1: Write the failing test**

  `test_expand_grid_smoke_dimensions` — with 3 `Question`s (all `corpus="bank-chat-system"`), the 4 conditions A-D, `models=["glm-4.7"]`, `seeds=[0]`, one `CorpusRecord` named `bank-chat-system`: `expand_grid(...)` returns a list of length `3*4*1*1 == 12`; every element is a `CellSpec`; the `[0]` element's `condition.id == "A"`, `model == "glm-4.7"`, `seed == 0`; the corpus attached is the `bank-chat-system` record.

  `test_expand_grid_unknown_corpus_raises` — a question whose `corpus` is not among the records → `ConfigError` naming the orphan corpus.

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_run_bench.py -v`
  Expected: FAIL — `ModuleNotFoundError: bench.run_bench`.

- [ ] **Step 3: Write minimal implementation**

  Create `bench/run_bench.py` with `ConfigError` and `expand_grid` per the Produces contract. Build a `name -> CorpusRecord` map; iterate the cross-product in the specified order.

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_run_bench.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/run_bench.py tests/bench/test_run_bench.py`
  Run: `git commit -m "feat(bench): grid expansion -> CellSpec list"`

---

### Task 7: Results write + idempotency/resume (I/O, in run_bench)

**Files:**
- Modify: `bench/run_bench.py`
- Modify: `tests/bench/test_run_bench.py`

**Interfaces:**
- Consumes: `bench.claude_runner.CellResult`, `to_cell_jsonl`, `run_id`.
- Produces:
  - `run_dir(out_root: str, timestamp: str) -> str` — `<out_root>/<timestamp>` (created if absent).
  - `cell_paths(run_dir: str, rid: str) -> tuple[str, str]` — `(transcript_path, cell_jsonl_path)` = `(<run_dir>/<rid>/transcript.jsonl`, `<run_dir>/<rid>/cell.jsonl`) and ensures `<run_dir>/<rid>/` exists.
  - `write_cell(run_dir: str, result: CellResult) -> None` — writes `result`'s transcript (the `transcript_path` referenced by `result` is already written by `run_cell`; this writes `<rid>/cell.jsonl` as one `json.dumps(to_cell_jsonl(result))` line, and appends the same line to `<run_dir>/cells.jsonl`).
  - `cell_completed(run_dir: str, rid: str) -> bool` — True iff `<run_dir>/<rid>/cell.jsonl` exists and is non-empty (the resume gate).

- [ ] **Step 1: Write the failing tests**

  `test_write_cell_creates_files` — with a tmp `run_dir`, a `CellResult` whose `run_id="bc-impl-01_D_glm-4.7_s0"` and `transcript_path` pointing at an already-written tmp transcript: `write_cell` creates `<run_dir>/bc-impl-01_D_glm-4.7_s0/cell.jsonl` (one valid JSON line) and appends one line to `<run_dir>/cells.jsonl`.

  `test_cell_completed_gate` — before `write_cell`, `cell_completed(run_dir, rid)` is False; after, True.

  `test_write_cell_idempotent_overwrite` — calling `write_cell` twice with the same `rid` overwrites `<rid>/cell.jsonl` (single line, latest content) and appends a second line to the aggregated `cells.jsonl` (append-only across cells; re-running a cell is allowed and just appends again — the per-cell `cell.jsonl` is the source of truth for resume).

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_run_bench.py -v`
  Expected: FAIL — the functions are not defined.

- [ ] **Step 3: Write minimal implementation**

  Add `run_dir`, `cell_paths`, `write_cell`, `cell_completed` to `bench/run_bench.py` per the Produces contract.

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_run_bench.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/run_bench.py tests/bench/test_run_bench.py`
  Run: `git commit -m "feat(bench): results write + idempotency/resume gate"`

---

### Task 8: CLI orchestration (run_bench main, integration)

**Files:**
- Modify: `bench/run_bench.py`
- Modify: `tests/bench/test_run_bench.py`

**Interfaces:**
- Consumes: `expand_grid`, `run_dir`, `cell_paths`, `write_cell`, `cell_completed` (Tasks 6-7), `bench.claude_runner.run_cell`, `CellSpec`, `run_id`.
- Produces:
  - `SMOKE_QUESTIONS = ["bc-impl-01", "bc-role-01", "bc-cs-01"]`, `SMOKE_MODELS = ["glm-4.7"]`, `SMOKE_SEEDS = [0]`, `SMOKE_TEMPERATURE = 0.0`, `DEFAULT_MAX_TURNS = 15`.
  - `run_grid(cells: list[CellSpec], run_dir_path: str, *, resume: bool, run_cell_fn) -> list[CellResult]` — for each cell: compute `rid = run_id(cell)`; if `resume and cell_completed(run_dir_path, rid)`: skip; else `transcript_path, _ = cell_paths(run_dir_path, rid)`, `result = run_cell_fn(cell, results_transcript_path=transcript_path)`, `write_cell(run_dir_path, result)`. `run_cell_fn` defaults to `claude_runner.run_cell` and is the DI seam for tests.
  - `main(argv: list[str] | None = None) -> int` — argparse CLI with flags: `--corpora` (default `bench/corpora.yml`), `--conditions` (default `bench/conditions.yml`), `--questions-glob` (default `bench/questions/*.jsonl`), `--out` (default `bench/results`), `--models` (comma list), `--seeds` (comma list of ints), `--temperature` (float), `--max-turns` (int, default 15), `--resume` (store_true), `--smoke` (store_true; pins `--models glm-4.7 --seeds 0 --temperature 0 --questions` to `SMOKE_QUESTIONS` filtered from the glob). Loads corpora/conditions/questions, expands the grid, creates `run_dir(out, timestamp)`, calls `run_grid`, prints a one-line summary (cells run / skipped), returns 0.

- [ ] **Step 1: Write the failing tests**

  `test_run_grid_skips_completed_when_resume` — build 2 `CellSpec`s; pass a `run_cell_fn` fake that records calls; pre-seed `<run_dir>/<rid1>/cell.jsonl` via `write_cell` for the first cell; call `run_grid(cells, run_dir, resume=True, run_cell_fn=fake)`. Assert the fake was called once (for cell 2 only), and `cells.jsonl` ends with cell 2's line.

  `test_run_grid_runs_all_when_no_resume` — same setup, `resume=False`: the fake is called for both cells.

  `test_main_smoke_end_to_end` — monkeypatch `claude_runner.run_cell` with a fake returning a canned `CellResult`; invoke `main(["--smoke", "--out", <tmp>])`. Assert: the grid has 12 cells (3 questions × 4 conditions × 1 model × 1 seed), the fake was called 12 times, `<tmp>/<ts>/cells.jsonl` has 12 lines, and `main` returns 0. (Use temp corpora/conditions/questions files or the real bank-chat ones if available locally; if using real files, mark `@pytest.mark.requires_claude` only if the fake is NOT used — here the fake replaces the binary, so no marker needed.)

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_run_bench.py -v`
  Expected: FAIL — `run_grid`/`main` not defined.

- [ ] **Step 3: Write minimal implementation**

  Add the `SMOKE_*` constants, `run_grid`, and `main` to `bench/run_bench.py` per the Produces contract. Use `argparse`, `time.strftime` for the timestamp (`%Y%m%dT%H%M%S`), and `datetime` ISO timestamps for `started_at`/`finished_at` inside `run_cell` (already Task 5). Guard `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_run_bench.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  Run: `git add bench/run_bench.py tests/bench/test_run_bench.py`
  Run: `git commit -m "feat(bench): run_grid + CLI orchestration (--smoke, --resume)"`

---

### Task 9: Condition-C relabel + PREREGISTRATION amendment (docs + assertion)

**Files:**
- Modify: `bench/conditions.yml` (C entry `name` only)
- Modify: `bench/PREREGISTRATION.md` (append amendment)
- Modify: `tests/bench/test_load_conditions.py` (add a C-isolation assertion)

**Interfaces:**
- Consumes: `bench.load_conditions.load_conditions`.
- Produces:
  - `conditions.yml` condition C `name` becomes `Raw agent + shell (no Grep tool, no MCP)`. The `allowed_tools` (`[Read, Glob, Bash]`), `disallowed_tools` (`[]`), and `mcp_servers` (`[]`) are UNCHANGED.
  - `PREREGISTRATION.md` gains a dated `## Amendment 2026-07-20 — condition C relabel` section recording: the spike finding (Bash permits DIY grep; "no Grep" was unenforceable), the relabel, that C's distinction from A is the absence of the purpose-built `Grep` tool (not absence of search capability), that enforcement monitors `tool_call_breakdown` for the `Grep` tool, and that the tool list is unchanged.
  - A new test `test_condition_C_isolation_shape` asserting `load_conditions()` returns a C with `id=="C"`, `name=="Raw agent + shell (no Grep tool, no MCP)"`, `allowed_tools==["Read","Glob","Bash"]`, `disallowed_tools==[]`, `mcp_servers==[]`.

- [ ] **Step 1: Write the failing test**

  Add `test_condition_C_isolation_shape` to `tests/bench/test_load_conditions.py` asserting the four field values above. The current `name` is `Raw agent (read/list only)`, so this fails.

- [ ] **Step 2: Run test to verify it fails**

  Run: `.venv/bin/pytest tests/bench/test_load_conditions.py::test_condition_C_isolation_shape -v`
  Expected: FAIL — name mismatch.

- [ ] **Step 3: Apply the relabel and amendment**

  Edit `bench/conditions.yml`: change only the C entry's `name` line. Edit `bench/PREREGISTRATION.md`: append the `## Amendment 2026-07-20` section described in Produces.

- [ ] **Step 4: Run test to verify it passes**

  Run: `.venv/bin/pytest tests/bench/test_load_conditions.py -v`
  Expected: PASS (all cases including the new one).

- [ ] **Step 5: Commit**

  Run: `git add bench/conditions.yml bench/PREREGISTRATION.md tests/bench/test_load_conditions.py`
  Run: `git commit -m "docs(bench): condition-C relabel (raw+shell) + PREREGISTRATION amendment"`

---

### Task 10: Smoke grid run on bank-chat (procedural acceptance)

Procedural — exercises the real `claude` binary against the real bank-chat index/checkout. No new unit tests; this is the end-to-end acceptance gate from the spec.

**Files:**
- Reference: `bench/run_bench.py` (`--smoke`), `bench/indexes/bank-chat-system/`, `bench/checkouts/bank-chat-system/`.
- Produces: a run under `bench/results/<timestamp>/` (gitignored) and a one-paragraph acceptance note appended to `bench/PHASE0_FINDINGS.md` under a new `## Plan 2a smoke grid` subsection.

- [ ] **Step 1: Confirm the bank-chat index + checkout are present**

  Run: `ls bench/indexes/bank-chat-system bench/checkouts/bank-chat-system`
  Expected: both exist (built in Plan 1). If absent, stop and rebuild per `bench/PHASE0_FINDINGS.md` (operator CLI `init` + `reprocess` with ABSOLUTE `--index-dir`).

- [ ] **Step 2: Run the smoke grid**

  Run: `.venv/bin/python -m bench.run_bench --smoke`
  Expected: exit 0; a one-line summary of 12 cells run, 0 skipped; a fresh `bench/results/<timestamp>/` dir with 12 `<run_id>/` cells and a 12-line `cells.jsonl`.

- [ ] **Step 3: Verify enforcement sanity**

  For every condition-B cell, inspect its `cell.jsonl` `tool_call_breakdown`: it must contain NO graph tool (`mcp__jrag__find/describe/neighbors/resolve`) — only `mcp__jrag__search` and/or `Read`. For every condition-C cell: `tool_call_breakdown` must contain no `Grep` entry. (A short script over `cells.jsonl` is fine.)

- [ ] **Step 4: Correctness eyeball (not formal grading)**

  For the condition-D `bc-impl-01` cell, compare `final_answer` against `bench/oracle/expected/bc-impl-01.json`'s FQN set (already proven 12/12 in the spike). For `bc-role-01` and `bc-cs-01` D cells, eyeball that the answer is structurally plausible (no formal grade — Plan 2b).

- [ ] **Step 5: Verify idempotency**

  Re-run `.venv/bin/python -m bench.run_bench --smoke --resume --out bench/results/<same-timestamp>`: the summary reports 12 skipped (no new API spend), `cells.jsonl` unchanged.

- [ ] **Step 6: Record the acceptance note + full suite**

  Append the `## Plan 2a smoke grid` subsection to `bench/PHASE0_FINDINGS.md` with: timestamp, 12 cells completed, enforcement-sanity result, bc-impl-01 D match status, idempotency result, total cost (sum of `total_cost_usd` if captured, else "see cells.jsonl"). Then run `.venv/bin/pytest tests/bench/ -q` and confirm all pass.

- [ ] **Step 7: Commit**

  Run: `git add bench/PHASE0_FINDINGS.md`
  Run: `git commit -m "chore(bench): Plan 2a smoke grid acceptance (12 cells, bank-chat)"`

---

## Plan 2a acceptance (definition of done)

- `bench/claude_runner.py` provides `parse_stream`, `materialize_mcp_config`, `CellSpec`/`build_argv`/`cell_cwd`/`run_id`, `CellResult`/`to_cell_jsonl`/`derive_exit_reason`/`choose_n_turns`, and `run_cell` — all unit-tested with fake-claude fixtures (no API in tests).
- `bench/run_bench.py` provides `expand_grid`, results I/O (`run_dir`/`cell_paths`/`write_cell`/`cell_completed`), `run_grid`, and the `main` CLI (`--smoke`, `--resume`).
- `conditions.yml` C relabeled; `PREREGISTRATION.md` amended.
- The 12-cell smoke grid on bank-chat completed; enforcement sanity (B has no graph calls, C has no Grep) holds; `bc-impl-01` D matches the oracle; idempotency confirmed.
- `.venv/bin/pytest tests/bench/ -q` passes (real-claude tests auto-skip if `claude` is absent; no honest test is masked).

## Sequenced follow-on plans (not detailed here)

- **Plan 2b — Grading (Phase 3):** `grade.py` (programmatic set/Jaccard/path/client-route graders + condition-blinded glm-5.2 LLM judge + κ harness), consuming the `cell.jsonl`/`cells.jsonl` this plan produces and filling the `grade` field.
- **Plan 3 — Execute (Phases 4-6):** the ~1,200-run grid (resolve temperature/seed control — `claude -p` exposes no flags, so determinism/seed mechanics need a settings.json probe or a redesign), ablation conditions D₂/D₃ added to `conditions.yml`/`corpora.yml` (driver needs no change), `report.py`, `bench/README.md`, CI smoke workflow.

## TL;DR

Plan 2a builds the condition-agnostic `run_bench.py` + `claude_runner.py` driver in 10 TDD tasks: a pure stream-json parser, MCP-config materialization, `CellSpec`→argv assembly (with the four spike corrections and no `--max-turns`/`--temperature`/`--seed`), the `CellResult` JSONL schema with exit-reason/n_turns rules, the `run_cell` subprocess runner with a driver-side turn cap (tested via fake-claude fixtures), grid expansion, idempotent/resumable results I/O, and the `--smoke`/`--resume` CLI. It ships the condition-C documentary relabel + PREREGISTRATION amendment, then proves itself on a 12-cell bank-chat smoke grid whose acceptance is enforcement-sanity (B: no graph calls; C: no Grep tool) and a `bc-impl-01` oracle match. Grading is Plan 2b; the full run is Plan 3.
