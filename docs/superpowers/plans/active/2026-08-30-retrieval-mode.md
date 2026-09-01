# Retrieval Mode (vectors | bm25) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any user on any platform choose keyword (BM25) search instead of vector search at `jrag install` time, persisted as `retrieval: vectors | bm25` and honored by indexing, search dispatch, and the watch daemon — no embedding-model download ever happens in `bm25` mode.

**Architecture:** One config knob resolved by `resolve_operator_config` (CLI > env > YAML > default) and republished into `os.environ`, mirroring the `embedding.model ↔ SBERT_MODEL ↔ --embedding-model` pattern. The installer asks the question before the embedding-model prompt and persists it to `.java-codebase-rag.yml`. Every vectors-phase call site (CLI lifecycle, installer init/update, MCP reprocess, watcher/daemon) consults the resolved mode *before* spawning cocoindex. `mcp_v2.search_v2` consults the mode before its import probe. No ranking code changes.

**Tech Stack:** Python 3.11+, argparse, PyYAML, questionary (interactive prompts), pytest. Spec: `docs/superpowers/specs/active/2026-08-30-retrieval-mode-design.md`.

## Global Constraints

- Public values are exactly `vectors` and `bm25`. Internal identifiers keep their existing `lexical` names (`search_lexical`, `lexical_mode`) — no renames.
- Default is `vectors`; precedence is CLI > env > YAML > built-in default.
- Names, verbatim: YAML key `retrieval:` (top level), env var `JAVA_CODEBASE_RAG_RETRIEVAL`, install flag `--retrieval`.
- `.java-codebase-rag*` on-disk names and `JAVA_CODEBASE_RAG_*` env vars are intentional backward compat — never rename them.
- Python: use `.venv/bin/python` / `.venv/bin/pip` (repo root) only. Editable install only; if behavior smells stale, run `.venv/bin/pip install -e ".[dev]"`.
- Before test runs: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts`. Never commit an index under `tests/`.
- Run only the relevant subset during development; the full suite once, in Task 8.
- Ruff line length 100, target py311.
- Out of scope: ranking/scoring changes, silent auto-fallback on download failure, hybrid weight tuning, MCP/watch restart automation.
- Commit message prefixes follow repo style (`feat:`, `test:`, `docs:`).

---

### Task 1: Config — `retrieval` resolution, validation, env publication

**Files:**
- Modify: `src/java_codebase_rag/config.py` (dataclass `ResolvedOperatorConfig` at 353-388; `apply_to_os_environ` 390-403; `subprocess_env` 405-416; `resolve_operator_config` 586-766)
- Test: `tests/package/test_config.py` (existing config-knob home; follows its class-per-knob pattern)

**Interfaces:**
- Consumes: `_pick_str(*, cli_val, env_key, yaml_dict, yaml_path, default) -> tuple[str, SettingSource]` (config.py:419-440) — existing, unchanged.
- Produces (all later tasks rely on these):
  - `ResolvedOperatorConfig.retrieval: str` — always `"vectors"` or `"bm25"`; appended as a defaulted field (`retrieval: str = "vectors"`) after the `watch_*` fields so existing constructors are unaffected.
  - `ResolvedOperatorConfig.retrieval_source: SettingSource` — defaulted `retrieval_source: SettingSource = "default"`.
  - `resolve_operator_config(..., cli_retrieval: str | None = None)` — new keyword param.
  - Resolution: `_pick_str(cli_val=cli_retrieval, env_key="JAVA_CODEBASE_RAG_RETRIEVAL", yaml_dict=yaml_dict, yaml_path=("retrieval",), default="vectors")`.
  - Validation: if the picked value is not `"vectors"`/`"bm25"`, print to stderr `jrag: retrieval={value!r} is not one of vectors/bm25; falling back to 'vectors'.` and use `("vectors", "default")` — mirrors `watch.backend` at config.py:721-727.
  - `apply_to_os_environ` sets `os.environ["JAVA_CODEBASE_RAG_RETRIEVAL"] = self.retrieval`; `subprocess_env` sets the same key in the returned dict.
  - New module-level helper `retrieval_mode_from_env(env: Mapping[str, str] | None = None) -> str`: reads `JAVA_CODEBASE_RAG_RETRIEVAL` from `env` (or `os.environ` when `env is None`); returns `"bm25"` iff the stripped value equals `"bm25"` exactly (case-sensitive; `"BM25"` → `"vectors"`); every other value including absent → `"vectors"`. Exported for `mcp_v2` / `server`.

- [ ] **Step 1: Write the failing tests**

Add a `TestRetrievalMode` class (plus a `retrieval_mode_from_env` test class) to `tests/package/test_config.py`. Scenarios and exact expected results:

1. No YAML/env/CLI (tmp_path with no config file): `cfg.retrieval == "vectors"`, `cfg.retrieval_source == "default"`.
2. YAML with `retrieval: bm25` (write via `YAML_CONFIG_FILENAMES[0]`): `"bm25"` / `"yaml"`.
3. `monkeypatch.setenv("JAVA_CODEBASE_RAG_RETRIEVAL", "bm25")` beats YAML `retrieval: vectors`: `"bm25"` / `"env"`.
4. `cli_retrieval="bm25"` beats env `JAVA_CODEBASE_RAG_RETRIEVAL=vectors`: `"bm25"` / `"cli"`.
5. YAML `retrieval: hybrid`: capsys/stderr captures the warning naming both valid values; result `"vectors"` / `"default"`.
6. `cfg.apply_to_os_environ()` (monkeypatched `os.environ`): `os.environ["JAVA_CODEBASE_RAG_RETRIEVAL"]` equals the resolved value afterwards.
7. `cfg.subprocess_env()` returns a dict containing the key with the resolved value.
8. `retrieval_mode_from_env`: `{"JAVA_CODEBASE_RAG_RETRIEVAL": "bm25"}` → `"bm25"`; `" bm25 "` → `"bm25"` (strip); `"BM25"` → `"vectors"`; `"vectors"` → `"vectors"`; `{}` → `"vectors"`; `None` env reads `os.environ` (set/unset via monkeypatch).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_config.py -k retrieval -v`
Expected: FAIL — `AttributeError` on `cfg.retrieval` / `ImportError` on `retrieval_mode_from_env`.

- [ ] **Step 3: Write minimal implementation**

Add the two defaulted fields, the `cli_retrieval` param, the `_pick_str` call + validation block (place it after the watch-knob validation at config.py:713-734 so both use the same graceful-degradation style), env publication in both methods, and the `retrieval_mode_from_env` helper. Behavior exactly as the Interfaces block specifies — no other resolution behavior changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_config.py -v`
Expected: PASS (all new + all pre-existing config tests).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/config.py tests/package/test_config.py && git commit -m "feat(config): retrieval mode knob (vectors|bm25) with env publication"`

---

### Task 2: Installer — `select_retrieval`, YAML persistence, wizard wiring, `--retrieval` flag

**Files:**
- Modify: `src/java_codebase_rag/installer.py` (new `select_retrieval` + `_retrieval_choices` beside `select_surface` 576-635; `generate_yaml_config` 967-1018; `run_install` 2074-2245, Stage 2 at 2138-2151 and signature)
- Modify: `src/java_codebase_rag/cli.py` (`_cmd_install` 651-664; install subparser block 972-1017)
- Test: Create `tests/package/test_installer_retrieval.py` (module docstring + structure follow `tests/package/test_installer_surface.py`)

**Interfaces:**
- Consumes: `select_surface` pattern (installer.py:576-635) — flag validation, non-interactive default, prefill cursor; `generate_yaml_config(source_root, model, microservice_roots, existing_yaml)` — existing signature; `vector_stack_installed()` from `pipeline.py` (installer.py:2139).
- Produces:
  - `select_retrieval(*, non_interactive: bool, cli_retrieval: str | None, prefill: str | None = None) -> Literal["vectors", "bm25"]`. Rules, in order: (1) `cli_retrieval` set — invalid value prints `Error: Invalid retrieval '{value}'. Must be 'vectors' or 'bm25'.` and raises `SystemExit(2)`; valid value returned as-is. (2) `non_interactive` — returns `"vectors"`. (3) Interactive — prints a one-line Note (`Note: 'vectors' needs an embedding model (auto-downloaded from Hugging Face, or a local path); 'bm25' is keyword search — no model, no downloads, works offline. In bm25 mode the sql/yaml tables are not searched (Java/Kotlin symbols only).`) then a questionary select via the shared `prompt()` helper with `_retrieval_choices()`: `[{"name": "vectors (Recommended)", "value": "vectors"}, {"name": "bm25", "value": "bm25"}]`, cursor default `prefill if prefill is not None else "vectors"`; empty answer returns the default.
  - `generate_yaml_config(..., retrieval: str = "vectors")` — new keyword-only param. When `retrieval == "bm25"`: sets `config["retrieval"] = "bm25"`. When `"vectors"`: removes a `retrieval` key from the copied existing config if present (mirror the `embedding.model` removal at installer.py:994-1004). All other key handling unchanged.
  - `run_install(..., retrieval: str | None = None)` — new keyword param, plumbed from `_cmd_install` as `retrieval=getattr(args, "retrieval", None)`.
  - Stage 2 replacement behavior (installer.py:2138-2151): first resolve the mode — if `not vector_stack_installed()`: print the existing skip message unchanged, force retrieval `"bm25"`, model `"auto"`; else `retrieval = select_retrieval(non_interactive=non_interactive, cli_retrieval=retrieval, prefill=(existing_config.get("retrieval") if existing_config else None))`. Then the model stage: if retrieval is `"bm25"` — print `Skipping embedding model selection: retrieval mode is bm25 (keyword search; no model needed).` and set `resolved_model = "auto"` (do NOT call `resolve_model`); else exactly today's `resolve_model(model, non_interactive=non_interactive)` call. `generate_yaml_config` gains `retrieval=retrieval` at the call site (installer.py:2213).
  - argparse (cli.py install block): `install.add_argument("--retrieval", choices=["vectors", "bm25"], default=None, help="Retrieval mode: 'vectors' (semantic search; requires an embedding model — auto-downloaded from Hugging Face or a local path) or 'bm25' (keyword search; no model, no downloads, works offline). Default: vectors.")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/package/test_installer_retrieval.py` with these scenarios and expected results:

1. `select_retrieval(cli_retrieval="bm25", non_interactive=True)` → `"bm25"` (no prompt — monkeypatch `installer.prompt` to raise if called).
2. `select_retrieval(cli_retrieval="hybrid", non_interactive=True)` → `SystemExit` code 2, message contains `Must be 'vectors' or 'bm25'`.
3. `select_retrieval(cli_retrieval=None, non_interactive=True)` → `"vectors"`.
4. Interactive (monkeypatch `sys.stdin.isatty` → True and `installer.prompt` to return the passed `default`): no prefill → `"vectors"`; prefill `"bm25"` → `"bm25"` (assert the `default` kwarg passed to `prompt` equals the prefill).
5. `generate_yaml_config(tmp_path, "auto", None, None, retrieval="bm25")` → YAML string contains a `retrieval: bm25` line.
6. `generate_yaml_config(tmp_path, "auto", None, None, retrieval="vectors")` → no `retrieval` line.
7. `generate_yaml_config(tmp_path, "auto", None, {"retrieval": "bm25", "cross_service_resolution": "brownfield_only"}, retrieval="vectors")` → output preserves `cross_service_resolution`, drops `retrieval` (switching modes on update re-run works).
8. Stage-2 wiring: call `run_install` with monkeypatched heavy stages (`confirm_source_root`, `detect_java_layout` → single module, `select_hosts`, `select_scope`, `select_surface`, `resolve_mcp_command`, `deploy_artifacts`, `_write_hosts_marker`, `update_gitignore`, `run_init_if_needed` all stubbed; `vector_stack_installed` → True) with `retrieval="bm25"`: assert `resolve_model` was NOT called (monkeypatch it to raise) and the written `.java-codebase-rag.yml` contains `retrieval: bm25`.
9. Same harness with `vector_stack_installed` → False: written YAML contains `retrieval: bm25` even when `retrieval=None` (Intel-Mac force), and the graph-only skip message was printed (capsys).
10. Same harness with `retrieval=None`, `vector_stack_installed` → True, non-interactive: YAML has no `retrieval` key (default vectors not persisted).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_installer_retrieval.py -v`
Expected: FAIL — `ImportError: cannot import name 'select_retrieval'` (and `TypeError` on the `retrieval` kwarg).

- [ ] **Step 3: Write minimal implementation**

Add `_retrieval_choices` + `select_retrieval` (modeled line-for-line on `select_surface`'s structure: flag check → non-interactive → Note print → prompt → fallback), extend `generate_yaml_config`, add the `retrieval` param to `run_install` and rewrite Stage 2 as specified, add the argparse flag and `_cmd_install` pass-through. No changes to `handle_rerun`, `deploy_artifacts`, or the marker file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_installer_retrieval.py tests/package/test_installer.py tests/package/test_installer_surface.py -v`
Expected: PASS (new module + both existing installer suites unaffected).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/installer.py src/java_codebase_rag/cli.py tests/package/test_installer_retrieval.py && git commit -m "feat(install): retrieval-mode wizard question, YAML persistence, --retrieval flag"`

---

### Task 3: CLI lifecycle — pre-spawn vectors skip (init / increment / reprocess)

**Files:**
- Modify: `src/java_codebase_rag/pipeline.py` (new constant beside `VECTORS_SKIPPED_GRAPH_ONLY`; cli.py imports it at cli.py:30)
- Modify: `src/java_codebase_rag/cli.py` (`_cmd_init` work() 370-435; `_cmd_increment` work() 448-540; `_cmd_reprocess` work() 548+)
- Test: Create `tests/package/test_retrieval_lifecycle.py`

**Interfaces:**
- Consumes: `cfg.retrieval` / `cfg.retrieval_source` from Task 1; `run_cocoindex_update`, `run_build_ast_graph`, `run_incremental_graph`, `_is_cocoindex_preflight_blocker` — all existing.
- Produces:
  - `pipeline.VECTORS_SKIPPED_BM25` — module constant, exact text: `jrag: vectors skipped — retrieval mode is bm25; building graph only.` (sibling of `VECTORS_SKIPPED_GRAPH_ONLY`, imported into cli.py the same way).
  - Behavior in all three commands' `work()`: compute `bm25_mode = cfg.retrieval == "bm25"` before any cocoindex call. When true: `run_cocoindex_update` is NOT called; print `VECTORS_SKIPPED_BM25` to stderr (same place the graph-only constant prints today); proceed to the graph phase exactly as the existing `vectors_skipped` branch does. When false: today's code path byte-for-byte.
  - Payload message strings (exact): init success under bm25 → `init completed (graph-only; vectors skipped — retrieval mode is bm25)`; increment success under bm25 → `increment completed (graph only; vectors skipped — retrieval mode is bm25)`; `increment --vectors-only` under bm25 → success `increment skipped: retrieval mode is bm25 (no vectors phase)`; `reprocess --vectors-only` under bm25 → success `reprocess skipped: retrieval mode is bm25 (no vectors phase)`; full reprocess under bm25 → graph-only rebuild with the init-style bm25 success message.
  - Existing graph-only (stack-absent) strings and logic are untouched.

- [ ] **Step 1: Write the failing tests**

Create `tests/package/test_retrieval_lifecycle.py`. Pattern: write a `.java-codebase-rag.yml` with `retrieval: bm25` into tmp_path, build an `argparse.Namespace` matching each command's expected attributes (see existing lifecycle tests in `tests/package/test_java_codebase_rag_cli.py` for the namespace shape — mirror it), monkeypatch `run_cocoindex_update` (raise AssertionError if called), `run_build_ast_graph` / `run_incremental_graph` (return a completed-process stub with `returncode=0`, empty stdout/stderr), and `_run_with_pipeline_progress` (call `work(None)` directly). Scenarios and expected results:

1. `_cmd_init` with bm25 YAML: exit 0; emitted JSON payload `success=True`, message contains `retrieval mode is bm25`; `run_cocoindex_update` never called; graph builder called once; stderr contains `VECTORS_SKIPPED_BM25` text.
2. `_cmd_init` with no `retrieval` key (or `retrieval: vectors`): `run_cocoindex_update` IS called once (today's behavior preserved).
3. `_cmd_increment` with bm25 YAML (no `--vectors-only`): exit 0, message `increment completed (graph only; vectors skipped — retrieval mode is bm25)`, cocoindex not called, `run_incremental_graph` called.
4. `_cmd_increment` with bm25 + `vectors_only=True`: exit 0, message `increment skipped: retrieval mode is bm25 (no vectors phase)`, neither cocoindex nor graph called.
5. `_cmd_reprocess` with bm25 + `vectors_only=True`: exit 0, message `reprocess skipped: retrieval mode is bm25 (no vectors phase)`, cocoindex not called.
6. `_cmd_reprocess` with bm25, full mode: graph-only rebuild path, cocoindex not called, exit 0.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_retrieval_lifecycle.py -v`
Expected: FAIL — cocoindex-called assertion fires (skip not implemented) / constant missing.

- [ ] **Step 3: Write minimal implementation**

Add the constant to `pipeline.py`; in each of the three `work()` bodies, gate the `run_cocoindex_update` call on `cfg.retrieval == "vectors"`, emit `VECTORS_SKIPPED_BM25` on the bm25 branch, and thread a `bm25_mode` flag into the existing success/skip-message selection so the five exact strings above are produced. Do not touch the `_is_cocoindex_preflight_blocker` logic — it remains active on the vectors path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_retrieval_lifecycle.py tests/package/test_graph_only_boot.py tests/package/test_java_codebase_rag_cli.py -v`
Expected: PASS (new + graph-only + CLI suites).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/pipeline.py src/java_codebase_rag/cli.py tests/package/test_retrieval_lifecycle.py && git commit -m "feat(cli): skip vectors phase pre-spawn when retrieval mode is bm25"`

---

### Task 4: Installer init/update, MCP reprocess, watch — mode-aware skip and probes

**Files:**
- Modify: `src/java_codebase_rag/installer.py` (`run_init_if_needed` 1085-1210, decide after `resolve_operator_config` at 1130; `run_update` vectors sub-step at 1999-2033, decide after `cfg` resolution at 1961)
- Modify: `src/java_codebase_rag/mcp/server.py` (`run_refresh_pipeline` 420-449)
- Modify: `src/java_codebase_rag/watch/daemon.py` (`__init__` probe at 86; warm-up branch 147-166; state-label comment 106-112)
- Modify: `src/java_codebase_rag/watch/watcher.py` (probe at 149)
- Test: Create `tests/package/test_retrieval_refresh.py`; extend `tests/watch/test_daemon.py` and `tests/watch/test_watcher.py`

**Interfaces:**
- Consumes: `cfg.retrieval` (Task 1); `retrieval_mode_from_env` (Task 1) — used only in `server.py`, which has no `cfg` in `run_refresh_pipeline`; `VECTORS_SKIPPED_BM25` (Task 3); `vector_stack_installed()` (pipeline).
- Produces:
  - `run_init_if_needed`: after `cfg = resolve_operator_config(...)` (installer.py:1130), when `cfg.retrieval == "bm25"` do not call `run_cocoindex_update`; print `VECTORS_SKIPPED_BM25` to stderr; set the local skip flag so the footer/`index_ok` logic and `write_config_source_pointer` behave exactly as the existing `vectors_skipped` path does; graph build proceeds. No cocoindex progress event is emitted (renderer's vectors task stays unspawned — same invariant as server.py:434-435).
  - `run_update`: same decision after its `cfg` resolution — under bm25 skip `run_cocoindex_update`, print `VECTORS_SKIPPED_BM25`, run `run_incremental_graph` only (existing best-effort graph-failure semantics unchanged).
  - `run_refresh_pipeline` (server.py:428): condition becomes `if not vector_stack_installed() or retrieval_mode_from_env() == "bm25":` — under bm25 print `VECTORS_SKIPPED_BM25` (import from pipeline) instead of `VECTORS_SKIPPED_GRAPH_ONLY`, then graph-only phase as today. The env var is guaranteed present because `server.main()` resolves config and calls `cfg.apply_to_os_environ()` before serving (server.py:909-910), which now publishes the mode (Task 1).
  - `WatchDaemon.__init__` (daemon.py:86): `self._vector_enabled = vector_stack_installed() and cfg.retrieval == "vectors"`. Everything downstream keys off `_vector_enabled` unchanged: state-file `"mode"` label (daemon.py:112) automatically reads `"lexical"` under bm25; warm-up is skipped. Update the two comments (106-112, 147-153) to name both reasons (stack absent OR retrieval=bm25).
  - Daemon warm-up else-branch message (daemon.py:163-166): when `_vector_enabled` is False because of mode, print `jrag watch: retrieval mode is bm25 — serving lexical search`; when False because stack absent, keep today's `vector stack unavailable` message. (Distinguish by re-evaluating `vector_stack_installed()` at print time.)
  - `SourceWatcher.__init__` (watcher.py:149): `self._vector_enabled = vector_stack_installed() and cfg.retrieval == "vectors"` — the cocoindex reindex step is skipped under bm25, graph reindex still fires.

- [ ] **Step 1: Write the failing tests**

`tests/package/test_retrieval_refresh.py` scenarios (tmp_path project with `retrieval: bm25` YAML; monkeypatch subprocess runners to fail loudly if invoked):

1. `run_init_if_needed` under bm25 with an empty index dir: returns `True`; `run_cocoindex_update` never called; `run_build_ast_graph` called; stderr contains the bm25 skip text; config source pointer written.
2. `run_update` under bm25: `run_cocoindex_update` never called; `run_incremental_graph` called; exit 0.
3. `run_refresh_pipeline` under bm25 (monkeypatch env `JAVA_CODEBASE_RAG_RETRIEVAL=bm25` + `JAVA_CODEBASE_RAG_INDEX_DIR`/`_SOURCE_ROOT` to tmp): vectors phase not spawned (no cocoindex runner call), graph phase runs.
4. Control: same three calls with `retrieval: vectors` YAML — cocoindex IS invoked (today's behavior).

`tests/watch/test_daemon.py` additions (follow its existing construction-with-fake-cfg pattern):

5. Daemon with cfg `retrieval="bm25"` (stack installed — monkeypatch `vector_stack_installed` → True): `_vector_enabled` is False; state dict `"mode"` == `"lexical"`; `warm.model()` never called (monkeypatch to raise).

`tests/watch/test_watcher.py` additions:

6. `SourceWatcher` with cfg `retrieval="bm25"` + `vector_stack_installed` → True: `_vector_enabled` False; the reindex path skips the cocoindex step (assert via the existing reindex test seam in that module).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_retrieval_refresh.py tests/watch/test_daemon.py tests/watch/test_watcher.py -v`
Expected: FAIL — cocoindex-called assertions fire; `_vector_enabled` True under bm25.

- [ ] **Step 3: Write minimal implementation**

Apply the five site changes exactly as the Produces block describes. Keep every existing stack-absent branch intact; the only shared-logic change is the compound `_vector_enabled` conditions and the added bm25 branches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_retrieval_refresh.py tests/watch/ tests/package/test_graph_only_boot.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/installer.py src/java_codebase_rag/mcp/server.py src/java_codebase_rag/watch/daemon.py src/java_codebase_rag/watch/watcher.py tests/package/test_retrieval_refresh.py tests/watch/test_daemon.py tests/watch/test_watcher.py && git commit -m "feat(index): retrieval-mode-aware vectors skip across installer, MCP reprocess, watch"`

---

### Task 5: Search dispatch — mode-first lexical + mode-aware advisories

**Files:**
- Modify: `src/java_codebase_rag/mcp/mcp_v2.py` (dispatch at 954-998)
- Test: `tests/mcp/test_mcp_v2.py` (extend; it already monkeypatches `run_search` per the seam documented at mcp_v2.py:64-68)

**Interfaces:**
- Consumes: `retrieval_mode_from_env` (Task 1); `run_lexical_search` (search_lexical, existing); `SearchOutput.lexical_mode` field (existing).
- Produces:
  - Dispatch rule at the top of the backend choice (mcp_v2.py:954): first evaluate `mode = retrieval_mode_from_env()`. If `mode == "bm25"`: take the lexical branch WITHOUT calling `_ensure_vector_backend()` (no `search_lancedb` import paid, `run_search` stays `_NOT_LOADED`), set `lexical_mode = True`. Otherwise: exactly today — `_ensure_vector_backend()` then `lexical_mode = run_search is None`. `SearchOutput.lexical_mode` is `True` on both lexical routes (the success return at mcp_v2.py:1134 already carries it).
  - Advisory strings (exact): chosen-mode lexical → `lexical mode (retrieval=bm25) — keyword ranking only; re-run jrag install and choose vectors to enable semantic search`; stack-absent lexical → today's string unchanged (mcp_v2.py:977-980). sql/yaml advisory under bm25 → `sql/yaml tables are not searched in bm25 (lexical) mode; only Java symbols were searched`; stack-absent variant keeps today's text. hybrid advisory → `hybrid is ignored in lexical mode` on both routes (replaces the graph-only-only wording; only the lexical branch prints it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/mcp/test_mcp_v2.py` (reuse its `ladybug_graph` fixture and `run_search` monkeypatch seam). Scenarios and expected results:

1. `monkeypatch.setenv("JAVA_CODEBASE_RAG_RETRIEVAL", "bm25")` + a working `ladybug_graph` + `run_search` monkeypatched to a value that is NOT `None` (e.g. a fake returning rows): result is still lexical — `output.lexical_mode is True`, advisories contain the exact `lexical mode (retrieval=bm25)…` string, and the fake `run_search` was never called (mode wins over the probe).
2. Same env + `table="sql"`: advisories contain the bm25 sql/yaml string.
3. Same env + `hybrid=True`: advisories contain `hybrid is ignored in lexical mode`.
4. Env unset, `run_search` monkeypatched to a rows-returning fake (existing tests' pattern): vector path — `lexical_mode is False`, no lexical advisory (regression guard).
5. Env unset, `run_search = None` (test-forced lexical): today's stack-absent advisory text still present (exact string from mcp_v2.py:977-980).
6. `JAVA_CODEBASE_RAG_RETRIEVAL=bm25` with `run_search` left at `_NOT_LOADED`: `_ensure_vector_backend` is not invoked — assert `run_search` is still `_NOT_LOADED` after the call (no vector import side effect).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/mcp/test_mcp_v2.py -k "retrieval or lexical" -v`
Expected: FAIL — mode ignored; vector path taken; advisory strings absent.

- [ ] **Step 3: Write minimal implementation**

Import `retrieval_mode_from_env` from config; restructure the backend choice per the Produces block (mode check first, then today's `_ensure_vector_backend` + probe); replace the three advisory strings per the exact texts, conditioned on which lexical route was taken (mode vs stack-absent). No changes to the vector branch's body (model load, TABLES, hybrid guard at mcp_v2.py:1003-1010 stay put).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/mcp/test_mcp_v2.py -v`
Expected: PASS (new + all existing dispatch tests).

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/mcp/mcp_v2.py tests/mcp/test_mcp_v2.py && git commit -m "feat(search): retrieval=bm25 forces lexical dispatch with mode-aware advisories"`

---

### Task 6: Remediation hints on vectors failures

**Files:**
- Modify: `src/java_codebase_rag/cli.py` (cocoindex failure payloads: `_cmd_init` 386-396, `_cmd_increment` 460-470, `_cmd_reprocess` vectors failure block)
- Modify: `src/java_codebase_rag/installer.py` (`run_init_if_needed` failure at 1166-1171)
- Modify: `src/java_codebase_rag/mcp/mcp_v2.py` (vector branch model load, mcp_v2.py:1011-1013)
- Modify: `src/java_codebase_rag/watch/daemon.py` (warm failure, daemon.py:157-161)
- Test: extend `tests/package/test_retrieval_lifecycle.py`, `tests/mcp/test_mcp_v2.py`, `tests/watch/test_daemon.py`

**Interfaces:**
- Consumes: the exact failure points listed above; no new symbols from other tasks beyond `retrieval_mode_from_env` (used to suppress the hint when mode is already bm25 — it can't fire there, but the guard keeps the hint honest).
- Produces — one shared hint constant in `pipeline.py`: `RETRIEVAL_BM25_HINT`, exact text:
  `Tip: can't download the embedding model? Switch to keyword search: jrag install --retrieval bm25 (or set JAVA_CODEBASE_RAG_RETRIEVAL=bm25) — indexing and search then work fully offline.`
  - CLI lifecycle + installer init/update: when the cocoindex phase fails (non-zero exit, not a preflight blocker), print `RETRIEVAL_BM25_HINT` to stderr immediately after the existing error payload emission.
  - `mcp_v2` vector branch: wrap only the `_get_sentence_transformer` call; on ANY exception from it, return `SearchOutput(success=False, message=f"embedding model load failed: {exc}. Switch to keyword search: re-run jrag install and choose bm25, or set JAVA_CODEBASE_RAG_RETRIEVAL=bm25.", advisories=[], limit=None, offset=None)` — bypassing the generic outer handler (mcp_v2.py:1137-1138) for this failure class only; all other exceptions keep today's envelope.
  - Daemon warm failure: after the existing `failed to load embedding model` stderr line (daemon.py:158), print `RETRIEVAL_BM25_HINT`.

- [ ] **Step 1: Write the failing tests**

1. `tests/package/test_retrieval_lifecycle.py`: `_cmd_init` with vectors-mode YAML + `run_cocoindex_update` stubbed to `returncode=1`: exit 1; stderr (capsys) contains the `Tip:` hint line.
2. Same file, control: bm25-mode YAML never reaches cocoindex, so no hint appears on any path.
3. `tests/package/test_retrieval_refresh.py` (or lifecycle module, wherever run_init test lives): `run_init_if_needed` with cocoindex stubbed to `returncode=1`: returns `False`; stderr contains the hint.
4. `tests/mcp/test_mcp_v2.py`: env unset (vectors mode), `_get_sentence_transformer` monkeypatched to raise `RuntimeError("no network")`: `search_v2` returns `success=False`, message starts with `embedding model load failed:` and contains `JAVA_CODEBASE_RAG_RETRIEVAL=bm25`.
5. `tests/mcp/test_mcp_v2.py`, control: a fake `run_search` raising `RuntimeError` (post-model failure) still yields today's plain `str(exc)` message — no hint, no wrapper text.
6. `tests/watch/test_daemon.py`: daemon with `_vector_enabled` forced True and `warm.model` raising: run path prints both the failure line and the `Tip:` hint.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_retrieval_lifecycle.py tests/package/test_retrieval_refresh.py tests/mcp/test_mcp_v2.py tests/watch/test_daemon.py -v`
Expected: FAIL — hint strings absent; generic envelope still returned.

- [ ] **Step 3: Write minimal implementation**

Add `RETRIEVAL_BM25_HINT` to `pipeline.py`; wire the four sites per the Produces block. In mcp_v2, the try/except wraps ONLY the model-load call — `_get_sentence_transformer` — not the surrounding vector search.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_retrieval_lifecycle.py tests/package/test_retrieval_refresh.py tests/mcp/test_mcp_v2.py tests/watch/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/pipeline.py src/java_codebase_rag/cli.py src/java_codebase_rag/installer.py src/java_codebase_rag/mcp/mcp_v2.py src/java_codebase_rag/watch/daemon.py tests/ && git commit -m "feat: bm25 remediation hint on embedding-model failures"`

---

### Task 7: Documentation

**Files:**
- Modify: `README.md` (~line 42), `docs/CONFIGURATION.md` (env-var table + YAML key table + §"Graph-only (macOS Intel) lexical ranking" at 460-472), `docs/ARCHITECTURE.md` (72, 87, 89), `docs/DESIGN.md` (~line 22), `docs/JRAG-CLI.md` (install flags + switching recipe)

**Interfaces:**
- Consumes: final strings from Tasks 1-6 (flag help, messages, advisory texts) — copy verbatim.
- Produces: doc statements only; no code.

- [ ] **Step 1: Update the five docs**

Content per doc:
1. `README.md` — the platform paragraph gains: on any platform the user may choose keyword (bm25) search at `jrag install` time (`--retrieval bm25`), which skips the embedding model entirely and works offline; Intel Mac remains graph-only by packaging.
2. `docs/CONFIGURATION.md` — add `JAVA_CODEBASE_RAG_RETRIEVAL` (values `vectors`/`bm25`, default `vectors`, precedence CLI > env > YAML, graceful fallback on invalid values) to the env-var section; add `retrieval:` to the YAML key table; retitle §460-472 "Graph-only (macOS Intel) lexical ranking" → "Lexical mode (bm25 or graph-only)" and add: reachable by user choice on every platform; sql/yaml not searched; switching recipes (vectors→bm25: no reindex; bm25→vectors: `jrag reprocess`; restart MCP/watch after either).
3. `docs/ARCHITECTURE.md` — at 72 and 87: dispatch is config-first (`retrieval=bm25` forces lexical) with the import probe as the safety net; at 89: note BM25 standalone mode reuses the same lexical backend.
4. `docs/DESIGN.md` — extend the line-22 statement: lexical is also a user-selectable primary mode, not only a fallback/third signal.
5. `docs/JRAG-CLI.md` — document `jrag install --retrieval {vectors,bm25}` in the install workflow and add the mode-switching recipe (edit YAML or re-run install; `jrag reprocess` when enabling vectors; restart server/daemon).

- [ ] **Step 2: Artifact audit**

Run: `grep -rn "Apple Silicon" install_data/ skills/ agents/ src/ docs/ README.md`
Expected: only intentional occurrences remain (the stack-absent advisory in `mcp_v2.py` and the platform notes in docs/README that are genuinely about install-time availability). No shipped artifact (`install_data/`, `skills/`, `agents/`) carries stale wording. Fix any that do — repo is the source of truth for deployed copies.

- [ ] **Step 3: Verify docs test if any**

Run: `.venv/bin/python -m pytest tests/test_docs_watch.py tests/package/test_install_data_sync.py -v`
Expected: PASS (docs-watch invariants + artifact sync checks).

- [ ] **Step 4: Commit**

Run: `git add README.md docs/ && git commit -m "docs: retrieval mode (vectors|bm25) across operator and internal docs"`

---

### Task 8: Full suite + final verification

**Files:** none (verification only)

**Interfaces:**
- Consumes: everything above.
- Produces: a green full suite on this worktree.

- [ ] **Step 1: Clean stale indexes**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.yml tests/*/.java-codebase-rag.hosts`
Expected: no output.

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — zero failures (baseline was green; every task kept its subset green).

- [ ] **Step 3: Lint**

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: no findings.

- [ ] **Step 4: Manual smoke (interactive wizard)**

Run in a scratch Java repo: `.venv/bin/jrag install --non-interactive --agent claude-code --surface cli --retrieval bm25` against a temp copy of a small fixture (e.g. `tests/bank-chat-system`), then `.venv/bin/jrag search "payment"` from that directory.
Expected: YAML contains `retrieval: bm25`; indexing never touches cocoindex; search returns hits with the `lexical mode (retrieval=bm25)` advisory. Then re-run install with `--retrieval vectors` and `jrag reprocess`: vector path resumes (on this arm64 dev machine).

- [ ] **Step 5: Report**

Report results truthfully (suite counts, lint, smoke outcomes). No commit needed unless fixes were made — commit any fix with `fix: …`.

---

## Self-Review (completed during planning)

- **Code scan:** no method bodies or algorithms; only exact strings, signatures, and behavior contracts.
- **Self-containment:** every task carries its full Interfaces block; no "see spec" references.
- **Spec coverage:** D1→Tasks 1,5 (values, no renames); D2→Task 1; D3→Task 2; D4→Tasks 3,4 (installer `run_update` included — a vectors-phase call site the spec's D4 list implied via "every call site"); D5→Tasks 4,5; D6→Task 6; D7→Task 7 (switching recipes documented); D8→Tasks 5 (advisory), 7 (docs). Compatibility (no rebuild, default unchanged) → Tasks 1-3 control tests + Task 8.
- **Placeholders:** none — every test and behavior names its scenario and expected result.
- **Type consistency:** `retrieval_mode_from_env` spelled identically in Tasks 1, 4, 5; `VECTORS_SKIPPED_BM25` in Tasks 3, 4; `RETRIEVAL_BM25_HINT` in Task 6; `select_retrieval`/`generate_yaml_config(retrieval=…)` in Task 2 only (no later task re-defines them).
