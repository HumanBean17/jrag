# Benchmark Foundation Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the benchmark foundation — frozen corpora, executable condition-isolation spec, locked prompts, an independent oracle that emits ground-truth answers (with a calibration gate), and the ~50-question golden set — so that Plan 2 (the agent harness) can run agent cells against a frozen, auditable ground truth.

**Architecture:** A `bench/` Python package owns declarative configs (`corpora.yml`, `conditions.yml`, `questions/*.jsonl`) and the oracle pipeline. Configs are validated by loaders that also encode methodological invariants (graph tools denied in condition B; question text free of jrag vocabulary). The oracle merges a *mechanical* source (jqassistant over Neo4j, cross-checked by jdeps) with a *manual* expert source, then a calibration gate diffs mechanical-vs-manual on the bank-chat fixture before the mechanical oracle is trusted on the large corpora.

**Tech Stack:** Python 3 (`.venv/bin/python`), pytest, PyYAML, jqassistant (Neo4j over javaparser/ASM), jdeps (JDK), the `java-codebase-rag` operator CLI for index builds.

## Global Constraints

- Use `.venv/bin/python` and `.venv/bin/pip` only — never system `python`/`pip`. Editable install of `java-codebase-rag` is already enforced by `tests/conftest.py`.
- Run only the relevant pytest subset during development; run the full suite once at the end of the task.
- Erase stale manual indexes under `tests/` before running anything index-related: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`. Bench indexes live under `bench/`, never `tests/`.
- Bench code is a standalone package (`bench/`) imported as `import bench.<module>`; it must not be folded into the `java_codebase_rag` distribution.
- Pin every external corpus to a commit SHA; bank-chat-system is a local fixture pinned to this repo's SHA.
- New Python deps (e.g. PyYAML if not present) are added via `.venv/bin/pip install -e ".[dev]"` after editing `pyproject.toml` `[dev]` extras — do not install into the environment ad hoc.

---

## File Structure (Plan 1 deliverables)

```
bench/
  __init__.py
  corpora.yml                              # corpus registry (name -> source + index manifest)
  conditions.yml                           # executable isolation spec (A/B/C/D)
  mcp/jrag.json                            # --mcp-config payload for the jrag server
  prompts/
    _shared_skeleton.md                    # shared preamble (problem statement, output format)
    A_lexical.md, B_vector_only.md, C_raw_agent.md, D_jrag_full.md
  questions/
    bank-chat-system.jsonl
    shopizer.jsonl
    spring-petclinic-microservices.jsonl
  oracle/
    __init__.py
    build_oracle.py                        # CLI: merge sources -> expected/<id>.json; --calibrate
    calibration.py                         # diff mechanical-vs-manual, agreement %, pass/fail
    jqa_runner.py                          # run a .cypher rule against a scanned corpus
    jdeps_runner.py                        # run jdeps, parse class-dep pairs
    jqassistant_rules/
      implements.cypher, injects.cypher, calls_in.cypher, calls_out.cypher,
      role_controllers.cypher, transitive_blast.cypher
    manual/
      bank-chat-system.json, shopizer.json, spring-petclinic-microservices.json
    expected/
      <question_id>.json                   # frozen ground truth
      _manifest.json                       # per-category + per-source counts
    calibration_report.json                # bank-chat gate output
    JQASSISTANT_COVERAGE.md                # Task 1 spike finding
  load_corpora.py                          # corpora.yml -> list[CorpusRecord], validate()
  load_conditions.py                       # conditions.yml -> list[Condition], to_flags() -> ConditionFlags
  load_questions.py                        # questions/*.jsonl -> list[Question], validate() (anti-leakage)
  checkout_corpora.py                      # clone/pin SHAs into bench/checkouts/<name>/
  PHASE0_FINDINGS.md                       # overall de-risk log (Task 1 + later spikes)
  PREREGISTRATION.md                       # frozen claims C1–C6 + question IDs
tests/
  bench/
    __init__.py
    conftest.py                            # puts repo root on sys.path so `import bench` works
    fixtures/synthetic/                    # tiny Java fixtures for rule/oracle tests
      implements_demo/  injects_demo/  calls_demo/  roles_demo/  blast_demo/
    test_load_corpora.py
    test_checkout_corpora.py
    test_load_conditions.py
    test_load_questions.py
    test_jqa_runner.py
    test_jdeps_runner.py
    test_build_oracle.py
    test_calibration.py
```

**Responsibility split.** Loaders (`load_corpora`, `load_conditions`, `load_questions`) are pure validators that encode invariants and emit typed records — no I/O beyond reading their config. `checkout_corpora` and `oracle/*` do I/O (git, jdeps, jqassistant, file writes). Grading and the agent driver are explicitly **not** in Plan 1 (they belong to Plan 2).

---

### Task 1: jqassistant injection-coverage spike (de-risk)

Resolves the one Plan-1-blocking unknown from the spec: whether jqassistant can independently resolve Spring DI (`@Autowired` field + constructor injection) well enough to serve as the `injects` and `upstream-consumers` oracle, or whether those categories must fall back to manual.

**Files:**
- Create: `bench/oracle/JQASSISTANT_COVERAGE.md`
- Reference: `tests/bank-chat-system` (find a class with constructor injection and one with `@Autowired`)

**Interfaces:**
- Produces: a written verdict in `JQASSISTANT_COVERAGE.md` with one of two outcomes —
  - `COVERED`: jqassistant resolves both injection styles; `injects`/`upstream-consumers` categories may use the mechanical oracle (`oracle_source: "jqassistant:injects.cypher"`).
  - `GAP`: named injection style(s) unresolved → those categories are manual-only (`oracle_source: "manual"`), and Task 7's `injects.cypher` is scoped accordingly.
  The verdict names the specific jqassistant concept/relationship used (e.g. `:ANNOTATED_BY`, `:WRITE`, parameter types of constructors) and includes one worked example: a bank-chat class FQN, the expected injected types, and the jqassistant query result that confirms them.

- [ ] **Step 1: Install/confirm jqassistant and a JDK**

Run: `.venv/bin/python -c "import shutil; print(shutil.which('java'))"` and `ls ~/jqassistant*/bin/jqassistant.sh 2>/dev/null || echo "need install"`
Expected: a JDK is present (jdeps also needs it). If jqassistant is absent, note the install step (download the CLI distribution) in the findings doc; do not commit a binary.

- [ ] **Step 2: Scan a slice of bank-chat into a Neo4j store**

Run jqassistant `scan` on one package of `tests/bank-chat-system` and `availableReport`/`server` to query. Use the interactive Cypher console against the scanned store.
Expected: the scan succeeds and nodes of type `:Type`/`:Method`/`:Field` are present.

- [ ] **Step 3: Probe constructor injection**

Query: find constructor parameters of a known service class and map each parameter type to a bean type.
Expected: the parameter types of the constructor are recoverable, demonstrating constructor-injection resolution.

- [ ] **Step 4: Probe `@Autowired` field injection**

Query: find fields annotated `@Autowired` (or `@Inject`) and their declared types.
Expected: either both annotation styles resolve (→ COVERED) or one does not (→ GAP, named).

- [ ] **Step 5: Write the verdict**

Record `COVERED` or `GAP` in `JQASSISTANT_COVERAGE.md` with the worked example (class FQN, expected injected types, confirming query result) and the exact jqassistant relationship names relied upon.
Expected: a reviewer can reproduce the two queries and reach the same verdict.

- [ ] **Step 6: Commit**

Run: `git add bench/oracle/JQASSISTANT_COVERAGE.md`
Run: `git commit -m "docs(bench): jqassistant injection-coverage verdict (Plan 1 de-risk)"`

---

### Task 2: bench package + corpora.yml schema + loader/validator

**Files:**
- Create: `bench/__init__.py` (empty marker)
- Create: `bench/corpora.yml`
- Create: `bench/load_corpora.py`
- Create: `tests/bench/__init__.py` (empty marker)
- Create: `tests/bench/conftest.py`
- Create: `tests/bench/test_load_corpora.py`

**Interfaces:**
- Produces:
  - `CorpusRecord` dataclass with fields: `name: str`, `source_kind: str` (`"git"` | `"local"`), `git_url: str | None`, `commit_sha: str | None`, `local_path: str | None`, `pinned_repo_sha: str | None`, `checkout_path: str`, `index: IndexManifest`.
  - `IndexManifest` dataclass: `index_dir: str`, `ontology_version: int`, `build_id: str | None`, `build_time_s: float | None`, `on_disk_bytes: int | None`.
  - `load_corpora(path: str = "bench/corpora.yml") -> list[CorpusRecord]` — reads YAML, constructs records, runs `validate` on each, returns list.
  - `validate(record: CorpusRecord) -> None` — raises `ConfigError` (a `bench.load_corpora` exception type) with a precise message on any violation.
  - Validation rules: `name` matches `^[a-z0-9-]+$` and is unique within the file; exactly one of `source_kind` channels is populated (`git` → `git_url`+`commit_sha` set; `local` → `local_path`+`pinned_repo_sha` set); `checkout_path` is under `bench/checkouts/`; `index.ontology_version` is a positive int.

- [ ] **Step 1: Write the failing test**

`test_load_corpora.py::test_loads_valid_corpora` — given a temp YAML file with one `git` corpus (bank-chat as `local` is covered separately) and one `git` corpus (shopizer) each satisfying the schema, `load_corpora(path)` returns two `CorpusRecord`s whose `name`, `source_kind`, `commit_sha`, and `index.ontology_version` match the file. Assert `checkout_path` starts with `bench/checkouts/`.

`test_load_corpora.py::test_rejects_duplicate_name` — two entries sharing a `name` → `load_corpora` raises `ConfigError` whose message contains "duplicate".

`test_load_corpora.py::test_rejects_git_missing_sha` — a `git` corpus with empty `commit_sha` → raises `ConfigError`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_load_corpora.py -v`
Expected: FAIL — `ModuleNotFoundError: bench.load_corpora`.

- [ ] **Step 3: Write minimal implementation**

`conftest.py` inserts the repo root onto `sys.path` so `import bench...` resolves. `load_corpora.py` defines `ConfigError`, the two dataclasses, `validate` enforcing the rules above, and `load_corpora` reading the YAML `corpora:` list and mapping each entry to a `CorpusRecord`. Use PyYAML; if absent, add it to `[dev]` extras and reinstall (see Global Constraints).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_load_corpora.py -v`
Expected: PASS.

- [ ] **Step 5: Seed corpora.yml**

Author `bench/corpora.yml` with three entries — bank-chat-system (`source_kind: local`, `local_path: tests/bank-chat-system`, `pinned_repo_sha: <current repo SHA>`), shopizer (`git`, real URL, `commit_sha: <to pin in Task 4>`), spring-petclinic-microservices (`git`, real URL, `commit_sha: <to pin in Task 4>`). Leave `build_id`/`build_time_s`/`on_disk_bytes` null until Task 4.

- [ ] **Step 6: Commit**

Run: `git add bench/__init__.py bench/corpora.yml bench/load_corpora.py tests/bench/__init__.py tests/bench/conftest.py tests/bench/test_load_corpora.py`
Run: `git commit -m "feat(bench): corpora.yml schema + loader/validator"`

---

### Task 3: checkout_corpora — clone & pin SHAs

**Files:**
- Create: `bench/checkout_corpora.py`
- Test: `tests/bench/test_checkout_corpora.py`

**Interfaces:**
- Consumes: `bench.load_corpora.load_corpora` and `CorpusRecord`.
- Produces: `checkout_all(corpora_path: str = "bench/corpora.yml", force: bool = False) -> dict[str, str]` mapping corpus `name` → absolute `checkout_path`.
  - For `git` corpora: `git clone` (or `git fetch`) then `git -c advice.detachedHead=false checkout <commit_sha>` into `checkout_path`. Idempotent: if the dir exists at the right SHA, do nothing unless `force`.
  - For `local` corpora: copy the `local_path` tree into `checkout_path` (excluding `target/`, `build/`); idempotent unless `force`.
  - Raises `CheckoutError` (module-specific) with corpus name + underlying error on failure.

- [ ] **Step 1: Write the failing test**

`test_checkout_corpora::test_local_copy_idempotent` — with a temp `local_path` containing one `.java` file, calling `checkout_all` with a temp `corpora.yml` copies the tree once; a second call without `force` is a no-op (assert mtime unchanged); `force=True` re-copies. Assert the target contains the `.java` file and no `target/` dir.
`test_checkout_corpora::test_git_pin` — using a temp self-initialized git repo (created via `git init`, one commit, known SHA) as the `git_url`, `checkout_all` produces a checkout whose `git rev-parse HEAD` equals the pinned SHA. (Uses a file:// URL; no network.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_checkout_corpora.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`checkout_corpora.py` implements `checkout_all` per the Produces contract: dispatch on `source_kind`, shell out to `git` for git corpora, `shutil.copytree` with an ignore rule for local corpora, detect "already at SHA" via `git rev-parse HEAD`, and `force` semantics. Subprocess errors are wrapped in `CheckoutError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_checkout_corpora.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add bench/checkout_corpora.py tests/bench/test_checkout_corpora.py`
Run: `git commit -m "feat(bench): corpus checkout/pin (git + local)"`

---

### Task 4: Index the three corpora and record C5 build-cost data

Procedural task (uses the existing operator CLI; no new code). Produces the pinned SHAs and the C5 (build-cost) measurements.

**Files:**
- Modify: `bench/corpora.yml` — fill `commit_sha` for shopizer + petclinic (and confirm bank-chat `pinned_repo_sha`), and fill each `index` block's `build_id`, `build_time_s`, `on_disk_bytes`.
- Reference: operator CLI at `.venv/bin/java-codebase-rag`.

**Interfaces:**
- Consumes: `bench.checkout_corpora.checkout_all`.
- Produces: three built indexes under `bench/indexes/<corpus>/`, each parseable by the jrag MCP at query time; the `IndexManifest` fields in `corpora.yml` fully populated so C5 metrics are captured before any agent run.

- [ ] **Step 1: Pin SHAs and check out**

Run: `.venv/bin/python -m bench.checkout_corpora`
Expected: three dirs under `bench/checkouts/`; `git -C bench/checkouts/shopizer rev-parse HEAD` matches the SHA written to `corpora.yml`; same for petclinic.

- [ ] **Step 2: Build each index**

For each corpus run: `rm -rf bench/indexes/<corpus> && .venv/bin/java-codebase-rag init --source-root bench/checkouts/<corpus> --index-dir bench/indexes/<corpus> 2>bench/indexes/<corpus>.init.stderr`
Expected: exit 0; the renderer reports `vectors · done` and `graph · done`. Capture wall time. Record any parse-error summary from stderr.

- [ ] **Step 3: Record C5 data**

For each index: compute `on_disk_bytes` (`du -sb bench/indexes/<corpus>`), set `build_time_s` from the timed wall, set `build_id` to a deterministic hash of `<corpus>:<commit_sha>:<ontology_version>` (read `ontology_version` from the index `meta`). Write all three back into `bench/corpora.yml`.

- [ ] **Step 4: Verify determinism (C4 seed)**

Re-run `init` for bank-chat into a second temp dir and diff node/edge counts (via the operator CLI `tables`/`meta` or a small query) against the first build.
Expected: counts identical. Record the pair in `bench/PHASE0_FINDINGS.md` under "C4 determinism (n=2 of 3)".

- [ ] **Step 5: Commit**

Run: `git add bench/corpora.yml bench/PHASE0_FINDINGS.md`
Run: `git commit -m "chore(bench): build 3 indexes, pin SHAs, record C5 build-cost + C4 n=2"`
(Do not commit `bench/checkouts/` or `bench/indexes/` — add them to `.gitignore`.)

---

### Task 5: conditions.yml — executable isolation spec + loader

The methodological core: condition isolation is enforced by harness flags, not prompts. The loader both validates the invariants and emits the exact `claude -p` flag arguments.

**Files:**
- Create: `bench/conditions.yml`
- Create: `bench/mcp/jrag.json` (the `--mcp-config` payload)
- Create: `bench/load_conditions.py`
- Test: `tests/bench/test_load_conditions.py`

**Interfaces:**
- Produces:
  - `Condition` dataclass: `id: str` (`A`|`B`|`C`|`D`), `name: str`, `mcp_servers: list[str]`, `allowed_tools: list[str]`, `disallowed_tools: list[str]`, `prompt_file: str`.
  - `ConditionFlags` dataclass: `mcp_config_arg: str | None` (path to pass to `--mcp-config`, or `None` when `mcp_servers` empty), `allowed_tools: list[str]`, `disallowed_tools: list[str]`, `append_system_prompt: str` (contents read from `prompt_file`).
  - `load_conditions(path: str = "bench/conditions.yml") -> list[Condition]`.
  - `to_flags(cond: Condition, jrag_mcp_config_path: str = "bench/mcp/jrag.json") -> ConditionFlags`.
  - Module constants: `JRAG_GRAPH_TOOLS = ["mcp__jrag__find","mcp__jrag__describe","mcp__jrag__neighbors","mcp__jrag__resolve"]`, `JRAG_VECTOR_TOOLS = ["mcp__jrag__search"]`, `ALL_JRAG_TOOLS = JRAG_GRAPH_TOOLS + JRAG_VECTOR_TOOLS`.
  - `validate(cond: Condition) -> None` enforcing: ids are exactly `{A,B,C,D}`; condition B's `disallowed_tools` is a superset of `JRAG_GRAPH_TOOLS` and contains no member of `JRAG_VECTOR_TOOLS`; condition D's `disallowed_tools` contains no member of `ALL_JRAG_TOOLS`; conditions A and C have empty `mcp_servers`; every `prompt_file` exists on disk.

- [ ] **Step 1: Write the failing test**

`test_load_conditions::test_flags_A_no_mcp` — `to_flags(cond_A).mcp_config_arg is None`; `allowed_tools == ["Grep","Glob","Read","Bash"]`; `disallowed_tools == []`.
`test_load_conditions::test_flags_B_denies_graph_keeps_vector` — `set(to_flags(cond_B).disallowed_tools) == set(JRAG_GRAPH_TOOLS)`; `mcp_config_arg == "bench/mcp/jrag.json"`; `search` not denied.
`test_load_conditions::test_flags_D_denies_nothing_of_jrag` — `set(to_flags(cond_D).disallowed_tools) & set(ALL_JRAG_TOOLS) == set()`.
`test_load_conditions::test_validate_rejects_B_keeping_a_graph_tool` — a condition B entry that omits `mcp__jrag__neighbors` from `disallowed_tools` → `validate` raises `ConfigError`.
`test_load_conditions::test_validate_rejects_C_with_mcp` — condition C with non-empty `mcp_servers` → raises `ConfigError`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_load_conditions.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`load_conditions.py` defines the constants, dataclasses, `validate` (the five invariants), `load_conditions` (YAML → `Condition` list, validating each), and `to_flags` (reads `prompt_file`, sets `mcp_config_arg` to the jrag config path iff `"jrag" in cond.mcp_servers` else `None`). `bench/mcp/jrag.json` is a valid MCP-config JSON pointing at the pre-built index server (contents defined here as a contract: `{"mcpServers":{"jrag":{...server spec pointing at bench/indexes/<corpus>...}}}` — the corpus binding is parameterized at run time by Plan 2's driver, not fixed in this file; for Plan 1, author a template and note the parameterization).

- [ ] **Step 4: Author conditions.yml**

Fill `bench/conditions.yml` exactly per the spec's condition table (A/B/C/D tool sets; B `disallowed_tools = JRAG_GRAPH_TOOLS`; A,C `mcp_servers: []`).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_load_conditions.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add bench/conditions.yml bench/mcp/jrag.json bench/load_conditions.py tests/bench/test_load_conditions.py`
Run: `git commit -m "feat(bench): executable condition isolation spec (A/B/C/D) + flag emitter"`

---

### Task 6: Locked prompt skeletons + differ-only-in-tools validator

**Files:**
- Create: `bench/prompts/_shared_skeleton.md`
- Create: `bench/prompts/A_lexical.md`, `B_vector_only.md`, `C_raw_agent.md`, `D_jrag_full.md`
- Modify: `bench/load_conditions.py` — add prompt-equality invariant helper.
- Test: `tests/bench/test_load_conditions.py` (add cases)

**Interfaces:**
- Produces:
  - Four condition prompts, each structurally `<shared preamble>\n\n## Your tools\n<condition-specific tools section>\n`. The shared preamble states: the task (answer one question about the codebase in the cwd), the required output format (a final `## Answer` block plus a short tool-justification), and that the agent must stop when it has a defensible answer.
  - `prompt_tools_section(path: str) -> str` — returns the body of the `## Your tools` section of a prompt file.
  - `prompt_preamble(path: str) -> str` — returns everything before `## Your tools`.
  - Invariant (asserted in a test, optionally in `validate`): across all four prompts, `prompt_preamble` is byte-identical.

- [ ] **Step 1: Write the failing test**

`test_load_conditions::test_preambles_identical` — for the four prompt paths, `prompt_preamble` returns equal strings.
`test_load_conditions::test_tools_sections_differ` — the four `prompt_tools_section` values are pairwise distinct and each names exactly the tools available to that condition (A: grep/ripgrep; B: jrag `search` only, graph tools explicitly described as unavailable; C: read/list only; D: all five jrag tools).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_load_conditions.py::test_preambles_identical tests/bench/test_load_conditions.py::test_tools_sections_differ -v`
Expected: FAIL — prompt files absent.

- [ ] **Step 3: Author the prompts**

Write `_shared_skeleton.md` (the preamble). Write the four condition files by appending a condition-specific `## Your tools` section to that preamble. The B section must explicitly state the graph tools are not available and that the agent has semantic search only (so a misconfigured harness is human-visible). Keep prompts free of leading-question phrasing.

- [ ] **Step 4: Add the helpers and run tests**

Add `prompt_tools_section`/`prompt_preamble` to `load_conditions.py` (split on the `## Your tools` marker).

Run: `.venv/bin/pytest tests/bench/test_load_conditions.py -v`
Expected: PASS (all cases including the two new ones).

- [ ] **Step 5: Commit**

Run: `git add bench/prompts/ bench/load_conditions.py tests/bench/test_load_conditions.py`
Run: `git commit -m "feat(bench): locked condition prompts (shared preamble + tools section)"`

---

### Task 7: jqassistant Cypher rules + per-rule tests on synthetic fixtures

**Files:**
- Create: `bench/oracle/jqassistant_rules/{implements,injects,calls_in,calls_out,role_controllers,transitive_blast}.cypher`
- Create: `tests/bench/fixtures/synthetic/{implements_demo,injects_demo,calls_demo,roles_demo,blast_demo}/` each with 2–4 tiny `.java` files whose relationships are known.
- Create: `bench/oracle/jqa_runner.py`
- Test: `tests/bench/test_jqa_runner.py`

**Interfaces:**
- Consumes: `bench.checkout_corpora` (for scan targets), Task 1's `JQASSISTANT_COVERAGE.md` verdict (scopes `injects.cypher`).
- Produces:
  - `run_rule(checkout_path: str, rule_path: str) -> list[dict]` — scans `checkout_path` into a temp Neo4j store, executes the Cypher in `rule_path`, returns the rows as a list of dicts (column name → value). Raises `OracleError` on scan/query failure.
  - Six `.cypher` files, each returning rows with a documented column set:
    - `implements.cypher` → rows `{implementer_fqn, interface_fqn}` for all `IMPLEMENTS`.
    - `injects.cypher` → rows `{injector_fqn, injected_type_fqn}` (scoped per Task 1 verdict; if GAP, this file documents the unsupported style and returns only what jqassistant resolves).
    - `calls_in.cypher` (param `:callee`) → rows `{caller_fqn}`.
    - `calls_out.cypher` (param `:caller`) → rows `{callee_fqn}`.
    - `role_controllers.cypher` → rows `{fqn}` for classes annotated `@RestController`/`@Controller`.
    - `transitive_blast.cypher` (param `:seed`) → rows `{impacted_fqn}` reachable via injects/calls edges to depth 2.

- [ ] **Step 1: Write the failing test (one scenario)**

`test_jqa_runner::test_implements_rule` — scan `fixtures/synthetic/implements_demo` (two classes `Cat`/`Dog` implementing `Animal`, plus an unrelated `Plant`), run `implements.cypher`, assert the returned `implementer_fqn` set equals `{...Cat, ...Dog}` for `interface_fqn == ...Animal`.
Mark this test `@pytest.mark.requires_jqa` so it's skippable if jqassistant isn't on the host (the suite must not hard-fail in CI without the JDK/jqassistant).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_jqa_runner.py -v`
Expected: FAIL (module absent) or SKIP if the marker's gate finds no jqassistant.

- [ ] **Step 3: Write minimal implementation + fixtures**

Create the synthetic fixtures (≤4 files each, fully described above). Implement `jqa_runner.run_rule` (scan to temp store, execute, return rows). Author `implements.cypher` to pass the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_jqa_runner.py::test_implements_rule -v`
Expected: PASS (or SKIP if jqassistant absent — confirm the skip is honest, not masking a code error).

- [ ] **Step 5: Repeat for the other five rules**

For each of `injects`, `calls_in`, `calls_out`, `role_controllers`, `transitive_blast`: write a one-scenario test against the matching fixture, run-to-fail, author the `.cypher` + extend `run_rule` if parameter passing is needed, run-to-pass. One commit per rule is fine.

- [ ] **Step 6: Commit**

Run: `git add bench/oracle/jqa_runner.py bench/oracle/jqassistant_rules/ tests/bench/fixtures/synthetic/ tests/bench/test_jqa_runner.py`
Run: `git commit -m "feat(bench): jqassistant oracle rules + synthetic-fixture tests"`

---

### Task 8: jdeps runner wrapper

**Files:**
- Create: `bench/oracle/jdeps_runner.py`
- Test: `tests/bench/test_jdeps_runner.py`
- Fixture: reuse `tests/bench/fixtures/synthetic/calls_demo/` compiled, OR document that this test requires a compiled `.class` tree (see step 1).

**Interfaces:**
- Consumes: a compiled corpus (jdeps reads `.class` files; the wrapper accepts a path to compiled classes).
- Produces: `run(classpath_root: str, package_prefix: str | None = None) -> set[tuple[str, str]]` — returns a set of `(dependent_class_fqn, dependency_class_fqn)` pairs parsed from `jdeps -v` output. Raises `OracleError` if `jdeps` is missing or exits non-zero.

- [ ] **Step 1: Write the failing test**

`test_jdeps_runner::test_parses_dependency_pairs` — given a precompiled fixture (two classes `A` depends on `B`), `run(classpath_root=fixture)` returns a set containing `(...A, ...B)`. Because compilation is a host requirement, mark `@pytest.mark.requires_jdk` and gate on `shutil.which("jdeps")`; document in the test docstring how to compile the fixture (`javac`).
Expected before implementation: FAIL (module absent) or SKIP (no jdeps).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_jdeps_runner.py -v`
Expected: FAIL or SKIP.

- [ ] **Step 3: Write minimal implementation**

`jdeps_runner.run` shells out to `jdeps -v <classpath_root>`, parses lines mapping a dependent class to its dependencies into FQNs, filters to the optional package prefix, and returns the pair set.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_jdeps_runner.py -v`
Expected: PASS (or honest SKIP).

- [ ] **Step 5: Commit**

Run: `git add bench/oracle/jdeps_runner.py tests/bench/test_jdeps_runner.py`
Run: `git commit -m "feat(bench): jdeps dependency-pair oracle wrapper"`

---

### Task 9: build_oracle merge pipeline

**Files:**
- Create: `bench/oracle/build_oracle.py`
- Create: `bench/oracle/__init__.py`
- Test: `tests/bench/test_build_oracle.py`

**Interfaces:**
- Consumes: `bench.load_questions.load_questions` + `Question`; `bench.oracle.jqa_runner.run_rule`; `bench.oracle.jdeps_runner.run`; the manual file schema `{questions: {<question_id>: {expected: <expected-shape>, rationale: str}}}`.
- Produces:
  - `Expected` shape (union, discriminated by `kind`): `{kind:"symbol_set", fqns:[str], ids:[str]}` | `{kind:"path", hops:[{fqn:str}]}` | `{kind:"client_route_pairs", pairs:[{client_fqn:str, route:str, target_service:str}]}` | `{kind:"absence", verdict:"not_in_project", proof:str}`.
  - `build_expected(corpus_checkout: str, questions: list[Question], rules_dir: str, classpath_root: str | None, manual_path: str, out_dir: str) -> Manifest`.
    For each question, dispatch on `oracle_source`: `jqassistant:<rule>.cypher` → run rule, shape the rows into `Expected` per the question's `expected.kind`; `jdeps` → derive from the pair set; `manual` → read from `manual_path`. Write `out_dir/<question_id>.json` as `{question_id, expected, oracle_source, derived_at}`. Unknown/unparseable source → raise `OracleError`.
  - `Manifest` dataclass: `per_category: dict[str,int]`, `per_source: dict[str,int]`, `total: int`, written to `out_dir/_manifest.json`.

- [ ] **Step 1: Write the failing test**

`test_build_oracle::test_merges_jqa_and_manual` — with a mocked `jqa_runner.run_rule` returning a known implements set for question `q-jqa`, and a manual file providing `expected` for question `q-man`, `build_expected` writes two `*.json` files with correct `expected` payloads and a `_manifest.json` showing `per_source = {"jqassistant":1,"manual":1}`.
`test_build_oracle::test_unknown_source_raises` — a question with `oracle_source: "astrology"` → `OracleError`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_build_oracle.py -v`
Expected: FAIL — module absent.

- [ ] **Step 3: Write minimal implementation**

`build_oracle.py` implements the dispatch, shaping (rows → `Expected` per `kind`), file writes, and manifest aggregation. Dependency-inject the runners so tests can mock them (no hard `import` of jqa/jdeps at call sites — pass them as parameters or use a small registry).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_build_oracle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add bench/oracle/__init__.py bench/oracle/build_oracle.py tests/bench/test_build_oracle.py`
Run: `git commit -m "feat(bench): oracle merge pipeline (jqassistant + jdeps + manual -> expected)"`

---

### Task 10: Calibration gate (mechanical-vs-manual on bank-chat)

The methodological guard: the mechanical oracle is not trusted on the large corpora until it agrees with the manual expert on bank-chat.

**Files:**
- Create: `bench/oracle/calibration.py`
- Test: `tests/bench/test_calibration.py`

**Interfaces:**
- Consumes: `bench.oracle.build_oracle.build_expected` (mechanical output) and `oracle/manual/bank-chat-system.json` (manual truth), restricted to the categories both sources cover.
- Produces:
  - `CalibrationReport` dataclass: `per_category: dict[str, Agreement]`, `overall: Agreement`, `threshold: float`, `passed: bool`.
  - `Agreement` dataclass: `match: int`, `total: int`, `ratio: float`.
  - `calibrate(corpus_checkout, questions, rules_dir, classpath_root, manual_path, threshold=0.9) -> CalibrationReport`.
    Set-comparison metric: for each question both sources answer, compare the FQN sets (or pair/path sets per `kind`) by exact equality; tally per category and overall. `passed = all(per_category[c].ratio >= threshold for c in per_category) and overall.ratio >= threshold`.
  - CLI: `python -m bench.oracle.build_oracle --calibrate --corpus bank-chat-system` writes `oracle/calibration_report.json` and exits 0 iff `passed` else 1.

- [ ] **Step 1: Write the failing test**

`test_calibration::test_passes_above_threshold` — synthetic mechanical + manual inputs agreeing on 9/10 and 10/10 across two categories at `threshold=0.9` → `passed is True`, `overall.ratio == 0.95`.
`test_calibration::test_fails_below_threshold` — 7/10 in one category → `passed is False`, the failing category named in the report.
`test_calibration::test_path_kind_uses_ordered_equality` — for a `kind:"path"` question, equal hops out of order → counted as a mismatch (path order matters).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_calibration.py -v`
Expected: FAIL — module absent.

- [ ] **Step 3: Write minimal implementation**

`calibration.py` implements the report types, the comparison (dispatch on `kind`: set equality for `symbol_set`/`client_route_pairs`, ordered equality for `path`, verdict equality for `absence`), and the CLI exit-code contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_calibration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add bench/oracle/calibration.py tests/bench/test_calibration.py`
Run: `git commit -m "feat(bench): oracle calibration gate (mechanical vs manual, per-category)"`

---

### Task 11: Question schema + loader/validator with anti-leakage check

**Files:**
- Create: `bench/load_questions.py`
- Test: `tests/bench/test_load_questions.py`

**Interfaces:**
- Produces:
  - `Question` dataclass: `id: str`, `corpus: str`, `category: str`, `difficulty: str`, `question: str`, `expected: Expected | None` (None until the oracle fills it), `oracle_source: str`, `claim_refs: list[str]`, `grading: str`.
  - Constants: `CATEGORIES = {"interface-impls","upstream-consumers","call-trace","blast-radius","cross-service","role-listing","semantic","absence"}`, `DIFFICULTIES = {"easy","medium","hard"}`, `CLAIMS = {"C1","C2","C3","C4","C5","C6"}`, `GRADINGS = {"programmatic_set_match","programmatic_jaccard","programmatic_path_match","programmatic_client_route_match","llm_judge","absence_check"}`, `LEAKAGE_VOCAB = {"INJECTS","IMPLEMENTS","EXTENDS","OVERRIDES","DECLARES","HTTP_CALLS","ASYNC_CALLS","EXPOSES","CALLS","edge_types","neighbors","NodeFilter","ontology_version","mcp__jrag"}`.
  - `load_questions(path: str) -> list[Question]` (one JSONL file) and `load_all_questions(glob: str = "bench/questions/*.jsonl") -> list[Question]`.
  - `validate(q: Question) -> None` enforcing: `id` matches `^[a-z0-9-]+$` and is globally unique; `corpus` ∈ loaded corpora names; `category`/`difficulty`/`grading` in their closed sets; `claim_refs ⊆ CLAIMS`; `question` is non-empty and contains **no** token from `LEAKAGE_VOCAB` (case-sensitive whole-token match). `ConfigError` with the offending token on violation.

- [ ] **Step 1: Write the failing test**

`test_load_questions::test_loads_valid_question` — a well-formed JSONL line → a `Question` with mapped fields.
`test_load_questions::test_rejects_leakage` — a `question` containing "which classes IMPLEMENTS the interface" → raises `ConfigError` whose message names `IMPLEMENTS`.
`test_load_questions::test_rejects_bad_category` — `category: "vibes"` → raises.
`test_load_questions::test_rejects_dup_id_across_files` — `load_all_questions` over two temp files sharing an id → raises.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bench/test_load_questions.py -v`
Expected: FAIL — module absent.

- [ ] **Step 3: Write minimal implementation**

`load_questions.py` defines constants, the dataclass, `validate` (incl. whole-token leakage scan), JSONL parsing, and `load_all_questions` with cross-file uniqueness.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bench/test_load_questions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add bench/load_questions.py tests/bench/test_load_questions.py`
Run: `git commit -m "feat(bench): question schema + loader/validator with anti-leakage check"`

---

### Task 12: Author the bank-chat-system golden question set

Procedural (human authoring). bank-chat carries the densest coverage incl. all cross-service questions.

**Files:**
- Create: `bench/questions/bank-chat-system.jsonl`

**Interfaces:**
- Consumes: `bench.load_questions.validate` (the gate), Task 1 verdict (scopes `injects` source), the bank-chat checkout.
- Produces: ~20 questions, engineer-phrased, covering: ≥3 `interface-impls`, ≥3 `upstream-consumers`, ≥2 `call-trace`, ≥2 `blast-radius`, **all** `cross-service` questions (≥6, since bank-chat is the cross-service carrier), ≥2 `role-listing`, ≥1 `semantic`, ≥1 `absence`. Each line's `expected` may be left `null` (Task 15 fills it via the oracle); `oracle_source` and `grading` are set per the taxonomy in the spec.

- [ ] **Step 1: Survey the bank-chat graph**

Using the built index (Task 4) via the jrag CLI (`find`/`describe`/`neighbors` — for *survey only*, never for ground truth), identify concrete anchors: an interface with multiple impls, a service injected into ≥2 controllers, an HTTP route hit from the other service, an async producer→route seam.
Expected: a shortlist of real FQNs/routes to phrase questions around. Do not copy tool vocabulary into the questions.

- [ ] **Step 2: Draft ~20 questions in engineer voice**

Write each as a human engineer would ask it. Cross-service questions must require resolving a seam between `chat-assign` and `chat-core`. Each record sets `category`, `difficulty`, `oracle_source` (`jqassistant:*` where covered, else `manual`), `claim_refs`, `grading`. Leave `expected: null`.

- [ ] **Step 3: Validate**

Run: `.venv/bin/python -c "from bench.load_questions import load_questions; [print(q.id) for q in load_questions('bench/questions/bank-chat-system.jsonl')]"`
Expected: prints ~20 ids with no `ConfigError` (incl. leakage check).

- [ ] **Step 4: Commit**

Run: `git add bench/questions/bank-chat-system.jsonl`
Run: `git commit -m "feat(bench): bank-chat golden question set (~20, incl. cross-service)"`

---

### Task 13: Author the shopizer + spring-petclinic-microservices question sets

Procedural. shopizer carries structural + semantic; petclinic carries structural + cross-service (Feign).

**Files:**
- Create: `bench/questions/shopizer.jsonl` (~15 questions)
- Create: `bench/questions/spring-petclinic-microservices.jsonl` (~15 questions)

**Interfaces:**
- Consumes: same as Task 12; petclinic's Feign clients are the cross-service anchors.
- Produces: ~15 questions each, balanced per the spec's distribution, engineer-phrased, `expected: null`, validated.

- [ ] **Step 1–3: Survey, draft, validate per corpus**

Repeat Task 12's survey→draft→validate for each corpus. Petclinic cross-service questions target Feign client → remote service route; shopizer semantic questions target real persistence/business flows.

- [ ] **Step 4: Commit**

Run: `git add bench/questions/shopizer.jsonl bench/questions/spring-petclinic-microservices.jsonl`
Run: `git commit -m "feat(bench): shopizer + petclinic golden question sets (~15 each)"`

---

### Task 14: Manual expert annotation — bank-chat-system

Procedural (human ground truth). Fills the manual source for bank-chat and serves as the calibration truth.

**Files:**
- Create: `bench/oracle/manual/bank-chat-system.json`

**Interfaces:**
- Consumes: `bench/questions/bank-chat-system.jsonl` (Task 12); the bank-chat checkout; Task 1 verdict (which categories are manual-only).
- Produces: a JSON object `{questions: {<id>: {expected: <Expected>, rationale: str}}}` covering **every** bank-chat question — including the mechanical-source ones, so the calibration gate (Task 16) has manual truth to diff against. `expected` matches the `Expected` shape from Task 9. `rationale` is one sentence citing the file/method that justifies the answer.

- [ ] **Step 1: Annotate structural questions from source**

For `interface-impls`/`upstream-consumers`/`role-listing`/`call-trace`/`blast-radius`, read the bank-chat source and record the exact FQN sets / paths. Cite the file in `rationale`.

- [ ] **Step 2: Annotate cross-service + absence questions**

For `cross-service`, record client→route pairs with `target_service`. For `absence`, record `verdict:"not_in_project"` and a `proof` describing what was searched and that nothing matched.

- [ ] **Step 3: Shape check**

Run a small validation (extend `test_build_oracle` or a one-off script) that every entry's `expected.kind` is valid and every bank-chat question id is present.
Expected: 100% coverage of bank-chat question ids.

- [ ] **Step 4: Commit**

Run: `git add bench/oracle/manual/bank-chat-system.json`
Run: `git commit -m "feat(bench): manual ground truth for bank-chat (calibration truth)"`

---

### Task 15: Run build_oracle + calibration gate on bank-chat (acceptance gate)

The gate that earns the mechanical oracle the right to be trusted on the large corpora.

**Files:**
- Modify: `bench/oracle/calibration_report.json` (generated).
- Reference: `bench/oracle/build_oracle.py` CLI.

**Interfaces:**
- Consumes: Tasks 9, 10, 12, 14.
- Produces: `bench/oracle/expected/bc-*.json` for all bank-chat questions; a passing `calibration_report.json` (every category ≥ threshold).

- [ ] **Step 1: Build expected for bank-chat**

Run: `.venv/bin/python -m bench.oracle.build_oracle --corpus bank-chat-system --out bench/oracle/expected`
Expected: one `*.json` per bank-chat question + `_manifest.json`; no `OracleError`.

- [ ] **Step 2: Run the calibration gate**

Run: `.venv/bin/python -m bench.oracle.build_oracle --calibrate --corpus bank-chat-system`
Expected: exit 0; `calibration_report.json` shows every category `ratio >= 0.9`. If any category fails, do not lower the threshold — investigate the divergence (jqassistant rule bug vs manual error), fix, re-run.

- [ ] **Step 3: Commit**

Run: `git add bench/oracle/expected/bc-*.json bench/oracle/expected/_manifest.json bench/oracle/calibration_report.json`
Run: `git commit -m "chore(bench): bank-chat expected answers + calibration gate passed"`

---

### Task 16: Manual annotation — shopizer + petclinic; build expected for all corpora

Procedural. Extends ground truth to the large corpora and freezes it.

**Files:**
- Create: `bench/oracle/manual/shopizer.json`, `bench/oracle/manual/spring-petclinic-microservices.json`
- Modify: `bench/oracle/expected/` (generated for shopizer + petclinic).

**Interfaces:**
- Consumes: Tasks 13, 9.
- Produces: manual truth for the manual-only categories on shopizer + petclinic (mechanical categories are filled by `build_oracle`); the complete `expected/` set across all three corpora, frozen.

- [ ] **Step 1: Annotate manual-only categories per corpus**

Using Task 1's verdict, annotate only the categories jqassistant/jdeps cannot cover (cross-service, absence, and any GAP injection). Structural categories are filled mechanically; do not duplicate.

- [ ] **Step 2: Build expected for shopizer + petclinic**

Run: `.venv/bin/python -m bench.oracle.build_oracle --corpus shopizer --out bench/oracle/expected` and the same for petclinic.
Expected: `*.json` per question; `_manifest.json` updated.

- [ ] **Step 3: Completeness check**

Assert every question id across `bench/questions/*.jsonl` has a corresponding `bench/oracle/expected/<id>.json` with non-null `expected`.
Expected: zero missing.

- [ ] **Step 4: Commit**

Run: `git add bench/oracle/manual/shopizer.json bench/oracle/manual/spring-petclinic-microservices.json bench/oracle/expected/`
Run: `git commit -m "feat(bench): shopizer+petclinic ground truth; freeze all expected answers"`

---

### Task 17: PREREGISTRATION — freeze claims + question inventory

Pre-registration discipline: the claims and the question inventory are frozen before any agent run (Plan 2/3), so post-hoc metric selection is impossible.

**Files:**
- Create: `bench/PREREGISTRATION.md`

**Interfaces:**
- Consumes: the approved spec; `bench.load_questions.load_all_questions`; `bench/oracle/expected/_manifest.json`.
- Produces: a document recording (a) claims C1–C6 verbatim with their metrics and question subsets, (b) the full question inventory (id, corpus, category, claim_refs) generated from the live files, (c) the grading-rubric *stub* (the locked judge rubric is finalized in Plan 2 with the grader; here we freeze only which questions are programmatic vs judge-graded), (d) the frozen `ontology_version`, corpus SHAs, and bench-tool version, (e) the honesty commitments (semantic category expected to tie/lose; raw logs will be published).

- [ ] **Step 1: Generate the inventory section**

Run a one-off that dumps `load_all_questions()` to the markdown table. Paste into the doc. Do not hand-edit the table — it is derived so it cannot drift from the files.

- [ ] **Step 2: Write claims + commitments**

Record C1–C6 and the honesty commitments. Mark the rubric as "programmatic vs judge-graded split frozen; full rubric finalized in Plan 2."

- [ ] **Step 3: Commit**

Run: `git add bench/PREREGISTRATION.md`
Run: `git commit -m "docs(bench): pre-register claims C1–C6 + question inventory"`

---

## Plan 1 acceptance (definition of done)

- Three corpora checked out at pinned SHAs; three indexes built; C5 (build time/size) and C4 (n=2 determinism) recorded.
- `conditions.yml` loads and `to_flags` produces the exact A/B/C/D flag sets; prompt preambles byte-identical.
- jqassistant rules pass on synthetic fixtures; jdeps wrapper parses dependency pairs.
- `build_oracle` produces an `expected/<id>.json` for every question across all three corpora; `calibration_report.json` passes (all categories ≥ 0.9) on bank-chat.
- ~50 golden questions authored, engineer-phrased, leakage-free, validated.
- `PREREGISTRATION.md` freezes claims + inventory.
- `.venv/bin/pytest tests/bench/ -v` passes (jqa/jdeps tests may SKIP on hosts lacking the tools; no honest test is masked).

## Sequenced follow-on plans (not detailed here)

- **Plan 2 — Harness (Phases 2–3):** `run_bench.py` driver (`claude -p` headless, transcript→JSONL, the JSONL schema from the spec), `grade.py` (programmatic graders + glm-5.2 blinded judge + κ harness), ablation-toggle confirmation spike. Gated on Plan 1's `conditions.yml`/`to_flags` and the `claude -p` flag-stability finding (a Phase-0 spike recorded in `bench/PHASE0_FINDINGS.md`).
- **Plan 3 — Execute (Phases 4–6):** the 1,200-run grid, ablations (D₂/D₃/D₄ where toggles exist), `report.py` (tables/plots), `bench/README.md`, CI smoke workflow, optional glm-4.5-air 3rd subject to power up C6.

## TL;DR

Plan 1 builds the auditable ground-truth foundation: pin 3 corpora, build indexes (capturing C4/C5 data), encode condition isolation as an executable `conditions.yml`, author locked prompts, build an independent oracle (jqassistant + jdeps + manual) with a bank-chat calibration gate, author ~50 leakage-free golden questions, and pre-register claims C1–C6. It is 17 bite-sized, mostly-TDD tasks. Plans 2 (agent harness + grading) and 3 (full run + ablations + report) are written after Plan 1 de-risks the `claude -p` and ablation-toggle unknowns.
