# jrag Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the tool's external brand to `jrag`, published as the `jrag-cli` PyPI package with a unified `jrag` CLI (operator + agent verbs) and `java-codebase-rag` kept as a backward-compatible shim — while leaving the internal Python module and all on-disk/env-var state untouched.

**Architecture:** Two-package split from one source tree: `jrag-cli` (root `pyproject.toml`, all code, four console scripts) and `java-codebase-rag` (new `shim/pyproject.toml`, no code, depends on `jrag-cli==<ver>`). A new `cli_dispatch` module routes `jrag <verb>` to the operator CLI (`cli`) or agent CLI (`jrag`) handler — their verb sets are disjoint, so routing is unambiguous. Old command names (`java-codebase-rag`, `java-codebase-rag-mcp`) become aliases through the same dispatcher / server entry point, emitting a TTY-gated deprecation nudge. The `java_codebase_rag` import module, all `.java-codebase-rag*` on-disk files, and all `JAVA_CODEBASE_RAG_*` env vars are deliberately unchanged.

**Tech Stack:** Python 3.11+, setuptools, argparse, pytest, tomllib.

## Global Constraints

(Copied verbatim from the spec — every task's requirements implicitly include these.)

- The internal import module stays `java_codebase_rag`. Do NOT rename it. No rewrite of import paths anywhere.
- On-disk names stay `.java-codebase-rag*` forever: the index dir `.java-codebase-rag/`, configs `.java-codebase-rag.yml` / `.java-codebase-rag.yaml`, marker `.java-codebase-rag.hosts`, `.java-codebase-rag/ignore`, `.java-codebase-rag/config_source`. No code reads or writes `.jrag*`.
- Env vars stay `JAVA_CODEBASE_RAG_*` (e.g. `JAVA_CODEBASE_RAG_INDEX_DIR`, `JAVA_CODEBASE_RAG_SOURCE_ROOT`). Do not rename or add `JRAG_*` aliases.
- The canonical PyPI package name is `jrag-cli` (the bare name `jrag` is taken on PyPI by a third party). Never document `pip install jrag`.
- Version is `0.12.0` (both `jrag-cli` and the `java-codebase-rag` shim, in lockstep).
- Legacy command aliases `java-codebase-rag` and `java-codebase-rag-mcp` MUST keep working identically for existing verbs.
- Use `.venv/bin/python` and `.venv/bin/pip` (repo-root venv). After any `pyproject.toml` change, run `.venv/bin/pip install -e ".[dev]"` so the editable install re-reads metadata.
- Tests build their own fresh index in a temp dir; never commit one under `tests/`. Before running tests, erase stale manual indexes: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}`.
- Conventional-commit style with a `rename` scope, e.g. `feat(rename): ...`, `docs(rename): ...`, `test(rename): ...`, `chore(rename): ...`.

## File Structure

**Created:**
- `src/java_codebase_rag/_deprecation.py` — legacy-alias deprecation helper (TTY + env gated).
- `src/java_codebase_rag/cli_dispatch.py` — unified `jrag` command router (operator↔agent verbs).
- `shim/pyproject.toml` — the `java-codebase-rag` shim package (no code, depends on `jrag-cli`).
- `docs/JRAG-CLI.md` — renamed from `docs/JAVA-CODEBASE-RAG-CLI.md`.
- `docs/MIGRATION.md` — short old→new migration note for users.

**Modified:**
- `pyproject.toml` — package name → `jrag-cli`, entry points, URLs, version, meta.
- `src/java_codebase_rag/_version.py` — `_PACKAGE` dist-name lookup → `jrag-cli`.
- `src/java_codebase_rag/mcp/server.py` — call the deprecation helper at the top of `main`.
- `tests/conftest.py` — prose only (command-name mentions in docstring/error message).
- `scripts/check_dist_version.py` + `tests/package/test_check_dist_version.py` — generalize filename-prefix derivation.
- `README.md`, `CLAUDE.md`, `mcp.json.example`, all `docs/*.md`, `skills/**`, `agents/**` (+ mirrored `install_data`), and user-visible display strings in `src/`.

**Task dependency graph (for parallel dispatch):**
- Core chain (sequential): Task 1 → Task 2 → Task 3 → Task 4 → Task 5.
- Task 6 (strings sweep) depends on the core chain (touches `src/` alongside it).
- Parallelizable after Task 3 lands (independent files, no conflicts): Tasks 7, 8, 9, 10, 11.
- Task 12 (final verification) depends on everything.

---

### Task 1: Legacy-alias deprecation helper

**Files:**
- Create: `src/java_codebase_rag/_deprecation.py`
- Create: `tests/package/test_deprecation.py`
- Modify: `src/java_codebase_rag/mcp/server.py` (top of `main`)

**Interfaces:**
- Consumes: none.
- Produces: `maybe_warn_legacy_alias(stream=None) -> None`. Behavior:
  - Computes the invoked program name from `os.path.basename(sys.argv[0])` when `sys.argv` is non-empty, else `""`.
  - If that name is exactly `java-codebase-rag` or `java-codebase-rag-mcp` (a legacy alias) AND `os.environ.get("JRAG_NO_DEPRECATION")` is falsy AND `sys.stderr.isatty()` is true: writes exactly one line to `stream` (default `sys.stderr`):
    `jrag: 'java-codebase-rag' is now 'jrag'; this alias continues to work. Set JRAG_NO_DEPRECATION=1 to silence.\n`
  - Otherwise: no output, no side effect.
  - Must be cheap and import-light (no backend imports) — it runs at MCP-server startup and before `--help`.
- Wiring contract: `mcp/server.py:main` calls `maybe_warn_legacy_alias()` as its first statement (before any other work). Under real MCP use stderr is not a TTY, so it is silent; the call exists for the rare human-debug case.

- [ ] **Step 1: Write failing tests**

`tests/package/test_deprecation.py` verifies, using `monkeypatch` to control `sys.argv`, `JRAG_NO_DEPRECATION`, and a fake `stderr` capturing writes via `monkeypatch.setattr(sys, "stderr", fake_stream_with_isatty_true)`:

1. `argv0 = "java-codebase-rag"`, no env, isatty True → the captured stream contains exactly the deprecation line (and only that line).
2. `argv0 = "java-codebase-rag-mcp"`, no env, isatty True → same line emitted.
3. `argv0 = "jrag"`, no env, isatty True → nothing emitted (canonical name, no warning).
4. `argv0 = "jrag-mcp"`, no env, isatty True → nothing emitted.
5. `argv0 = "java-codebase-rag"`, `JRAG_NO_DEPRECATION=1`, isatty True → nothing emitted (env suppresses).
6. `argv0 = "java-codebase-rag"`, no env, isatty False → nothing emitted (non-TTY suppresses).
7. `argv0 = "java-codebase-rag"`, no env, isatty True, `JRAG_NO_DEPRECATION="0"` → line IS emitted (only truthy values suppress; `"0"` is a non-empty string but treat the explicit values `"1"`, `"true"`, `"yes"` as suppress and everything else as not-suppress — pick the simpler rule "env var present and non-empty ⇒ suppress" and assert `"0"` suppresses too; document the chosen rule in the module docstring).

(For step 7, choose the rule "any non-empty value suppresses" and assert `"0"`, `"1"`, `"false"` all suppress. State this in the test.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/dmitry/Desktop/CursorProjects/java-enterprise-codebase-rag && .venv/bin/python -m pytest tests/package/test_deprecation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'java_codebase_rag._deprecation'` (or `ImportError`).

- [ ] **Step 3: Implement the helper**

Implement `maybe_warn_legacy_alias` per the Produces contract above. The check is: legacy alias detected (argv0 basename ∈ {`java-codebase-rag`, `java-codebase-rag-mcp`}) AND not suppressed. Suppression = `JRAG_NO_DEPRECATION` present and non-empty, OR `sys.stderr.isatty()` is false. On emit, write the single line to the `stream` argument (default `sys.stderr`). No exceptions raised under any input. Module docstring states the suppression rule.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_deprecation.py -v`
Expected: PASS — all 7 scenarios green.

- [ ] **Step 5: Wire into the MCP server**

In `src/java_codebase_rag/mcp/server.py`, add `from java_codebase_rag._deprecation import maybe_warn_legacy_alias` and call `maybe_warn_legacy_alias()` as the first statement inside `main`. Do not change any other behavior in `main`.

Verify: `.venv/bin/python -c "from java_codebase_rag.mcp.server import main; main"` imports cleanly; and `JRAG_NO_DEPRECATION=1 .venv/bin/python -c "import sys; sys.argv=['java-codebase-rag-mcp']; from java_codebase_rag._deprecation import maybe_warn_legacy_alias; maybe_warn_legacy_alias()"` prints nothing.

- [ ] **Step 6: Commit**

Run: `git add src/java_codebase_rag/_deprecation.py src/java_codebase_rag/mcp/server.py tests/package/test_deprecation.py`
Run: `git commit -m "feat(rename): add legacy-alias deprecation helper, wire into MCP server"`

---

### Task 2: Unified `jrag` CLI dispatcher

**Files:**
- Create: `src/java_codebase_rag/cli_dispatch.py`
- Create: `tests/package/test_cli_dispatch.py`

**Interfaces:**
- Consumes:
  - `java_codebase_rag._deprecation.maybe_warn_legacy_alias` (Task 1).
  - `java_codebase_rag.cli._console_script_main` and `java_codebase_rag.jrag._console_script_main` — both are zero-argument console-script entry functions that read `sys.argv` themselves, perform their own startup (`raise_fd_limit`, utf8 stdio, error handling), and call `sys.exit`.
- Produces: `java_codebase_rag.cli_dispatch._console_script_main() -> None` (calls `sys.exit`). Routing contract (the behavior an implementer must provide — the mechanism is theirs to choose):

  | Invocation (argv[0]) | First subcommand token in argv[1:] | Routes to |
  |---|---|---|
  | `jrag` | any operator verb | `cli._console_script_main` |
  | `jrag` | any agent verb | `jrag._console_script_main` |
  | `jrag` | none / `--help` / `--version` / unknown | `jrag._console_script_main` |
  | `java-codebase-rag` | any operator verb | `cli._console_script_main` |
  | `java-codebase-rag` | any agent verb | `jrag._console_script_main` |
  | `java-codebase-rag` | none / `--help` / `--version` / unknown | `cli._console_script_main` |

  - Operator verbs (frozenset, must match `cli.build_parser()` registered subcommands): `init`, `install`, `update`, `increment`, `reprocess`, `erase`, `meta`, `tables`, `diagnose`, `analyze-pr`, `unresolved`.
  - Agent verbs (frozenset, must match `jrag.build_parser()` registered subcommands): the 32 verbs registered in `jrag.py` (`find`, `search`, `inspect`, `callers`, `callees`, `hierarchy`, `watch`, `status`, …).
  - The dispatcher hands the full `sys.argv` (unchanged except `argv[0]` is left as-is) to the chosen module's `_console_script_main`, which re-reads `sys.argv`. The dispatcher does NOT re-implement argparse, fd-limit, or error handling.
  - Calls `maybe_warn_legacy_alias()` before routing.
  - Non-goal (documented): if a global flag's value token happens to equal a verb name (e.g. a hypothetical `--config find`), routing may follow the verb heuristic; acceptable edge case.

- [ ] **Step 1: Write failing tests**

`tests/package/test_cli_dispatch.py` uses `monkeypatch` to swap `cli._console_script_main` and `jrag._console_script_main` for recording stubs (so no real CLI/backend runs), and sets `sys.argv` per case. Assert the dispatcher routes to the right target. Cases (argv = `[prog, *rest]`):

1. `argv=["jrag","find","ChatController"]` → routes to jrag target. (agent verb)
2. `argv=["jrag","install"]` → routes to cli target. (operator verb under canonical name — unification)
3. `argv=["jrag","init"]` → routes to cli target.
4. `argv=["jrag","--version"]` → routes to jrag target. (no subcommand → identity default)
5. `argv=["jrag","--help"]` → routes to jrag target.
6. `argv=["jrag"]` → routes to jrag target. (no args)
7. `argv=["jrag","bogus-verb"]` → routes to jrag target. (unknown → identity default; the jrag parser then errors)
8. `argv=["java-codebase-rag","install"]` → routes to cli target.
9. `argv=["java-codebase-rag","find","X"]` → routes to jrag target. (alias gains agent verbs)
10. `argv=["java-codebase-rag","--version"]` → routes to cli target. (alias identity default)
11. `argv=["java-codebase-rag"]` → routes to cli target.
12. `argv=["jrag","--index-dir","/tmp/x","find","Y"]` → routes to jrag target. (global flag + value before the verb must not break routing)

Also add a test asserting the verb frozensets in `cli_dispatch` match the parsers' registered subcommands: build `cli.build_parser()` and `jrag.build_parser()`, collect each parser's subcommand choice names, and assert equality with `cli_dispatch`'s exported `OPERATOR_VERBS` / `AGENT_VERBS` frozensets. (This is the drift guard — if a verb is added to a parser but not to the dispatcher, this test fails.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_cli_dispatch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'java_codebase_rag.cli_dispatch'`.

- [ ] **Step 3: Implement the dispatcher**

Implement `cli_dispatch.py` exposing `OPERATOR_VERBS` and `AGENT_VERBS` frozensets (values exactly as listed above) and `_console_script_main` per the routing contract. The mechanism: read `sys.argv[0]` basename for identity; scan `sys.argv[1:]` for the first token that is a member of `OPERATOR_VERBS | AGENT_VERBS`; if found, route by its set; otherwise route by identity default. Call `maybe_warn_legacy_alias()` first, then the chosen target's `_console_script_main()` (let its `sys.exit` propagate).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_cli_dispatch.py -v`
Expected: PASS — all 12 routing cases + the drift-guard test green.

- [ ] **Step 5: Commit**

Run: `git add src/java_codebase_rag/cli_dispatch.py tests/package/test_cli_dispatch.py`
Run: `git commit -m "feat(rename): add unified jrag CLI dispatcher (operator+agent verbs)"`

---

### Task 3: Rename root package to `jrag-cli`, rewire entry points, bump version, update URLs

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `cli_dispatch._console_script_main` (Task 2), `mcp.server.main`.
- Produces: a root `pyproject.toml` whose `[project]` block has:
  - `name = "jrag-cli"`
  - `version = "0.12.0"`
  - `description = "jrag (formerly java-codebase-rag) — MCP server + jrag CLI for semantic + structural search over Java/Kotlin codebases"`
  - `keywords` includes `jrag` (lead), `java`, `mcp`, `rag`, `code-search`, `graph`, `lancedb`, `ladybug`.
  - `[project.urls]` Homepage / Repository / Issues all point at `https://github.com/HumanBean17/jrag` (Homepage/Repository) and `https://github.com/HumanBean17/jrag/issues` (Issues).
  - `[project.scripts]` exactly:
    ```
    jrag = "java_codebase_rag.cli_dispatch:_console_script_main"
    jrag-mcp = "java_codebase_rag.mcp.server:main"
    java-codebase-rag = "java_codebase_rag.cli_dispatch:_console_script_main"
    java-codebase-rag-mcp = "java_codebase_rag.mcp.server:main"
    ```
  - `[tool.setuptools.packages.find]` unchanged (`where = ["src"]`); `[tool.setuptools.package-data]` unchanged.
  - All `dependencies` and `optional-dependencies` unchanged.

- [ ] **Step 1: Make the edits**

Edit `pyproject.toml` to match the Produces contract. Do not touch dependencies, classifiers (except optionally adding `"Framework :: jrag"` — skip; leave classifiers as-is), package-data, or the `[tool.ruff]` block.

- [ ] **Step 2: Re-install editable and verify metadata**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: successful reinstall; the dist is now named `jrag-cli`.

Run: `.venv/bin/python -c "from importlib.metadata import version, distribution; print(version('jrag-cli')); print([ep.name for ep in distribution('jrag-cli').entry_points])"`
Expected: prints `0.12.0` and a list including `jrag`, `jrag-mcp`, `java-codebase-rag`, `java-codebase-rag-mcp`.

Run: `.venv/bin/python -c "from importlib.metadata import distribution; print([ep.value for ep in distribution('jrag-cli').entry_points if ep.name=='jrag'][0])"`
Expected: `java_codebase_rag.cli_dispatch:_console_script_main`.

- [ ] **Step 3: Verify the four console scripts resolve**

Run: `.venv/bin/jrag --version` → expected: prints `jrag 0.12.0 (python 3.x.y)`, exit 0.
Run: `.venv/bin/java-codebase-rag --version` → expected: prints `java-codebase-rag 0.12.0 (python 3.x.y)`, exit 0. (The legacy alias still works.)
Run: `.venv/bin/jrag-mcp --help` → expected: MCP server help, exit 0 (no crash).
Run: `.venv/bin/java-codebase-rag-mcp --help` → expected: MCP server help, exit 0.

- [ ] **Step 4: Run the existing version-flag and dispatch tests**

Run: `.venv/bin/python -m pytest tests/package/test_version_flag.py tests/package/test_cli_dispatch.py tests/package/test_deprecation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add pyproject.toml`
Run: `git commit -m "feat(rename): publish as jrag-cli, unify entry points, bump to 0.12.0"`

---

### Task 4: Fix dist-name lookup in `_version.py`; update `conftest.py` prose

**Files:**
- Modify: `src/java_codebase_rag/_version.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: the `jrag-cli` dist name from Task 3's installed metadata.
- Produces: `_version.py` reads the version from distribution `jrag-cli` (not `java-codebase-rag`); `package_version()` returns `"0.12.0"` under the editable install (never `"unknown"`). `conftest.py` prose mentions `jrag`/`jrag-cli` accurately; its editable-install enforcement logic is unchanged (it checks the import path, which is still `java_codebase_rag`).

- [ ] **Step 1: Write the failing test assertion (extend the existing version test)**

In `tests/package/test_version_flag.py`, the existing tests already assert `cli.main(["--version"])` output equals `version_string("java-codebase-rag")` and `jrag.main(["--version"])` equals `version_string("jrag")`. Add one new test `test_version_is_not_unknown` asserting `from java_codebase_rag._version import package_version; assert package_version() == "0.12.0"` (pins that the dist lookup resolves, not `unknown`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/package/test_version_flag.py::test_version_is_not_unknown -v`
Expected: FAIL — `package_version()` returns `"unknown"` (because `_PACKAGE` still points at `java-codebase-rag`, which no longer matches the installed dist `jrag-cli`) — OR it may already pass if Task 3's reinstall registered a `java-codebase-rag` dist too. If it passes already, that means a stale `java-codebase-rag` dist lingers from the pre-rename install; proceed anyway and fix `_version.py` so it reads `jrag-cli` directly (the correct source of truth).

- [ ] **Step 3: Edit `_version.py`**

Change `_PACKAGE = "java-codebase-rag"` to `_PACKAGE = "jrag-cli"`. Update the module docstring's parenthetical that names `java-codebase-rag` as the single source of truth to say `jrag-cli`. No other changes.

- [ ] **Step 4: Update `conftest.py` prose**

In `tests/conftest.py`, update only prose: the `_enforce_editable_install` docstring (around lines 43–48) and the error message string (around line 70) that reference "`jrag`/`java-codebase-rag` console scripts" — reword to reflect that the dist is `jrag-cli`, the unified command is `jrag`, and `java-codebase-rag` is a legacy alias. Do not change the enforcement logic (the `from java_codebase_rag import jrag` import check stays exactly as-is — the import module name is unchanged).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_version_flag.py -v`
Expected: PASS — all three version tests green, including `test_version_is_not_unknown`.

- [ ] **Step 6: Commit**

Run: `git add src/java_codebase_rag/_version.py tests/conftest.py tests/package/test_version_flag.py`
Run: `git commit -m "fix(rename): read version from jrag-cli dist; refresh conftest prose"`

---

### Task 5: Generalize the dist-version guard to derive the artifact prefix from `[project].name`

**Files:**
- Modify: `scripts/check_dist_version.py`
- Modify: `tests/package/test_check_dist_version.py`

**Interfaces:**
- Consumes: the root `pyproject.toml` (`name = "jrag-cli"`) and `shim/pyproject.toml` (`name = "java-codebase-rag"`, created in Task 7 — but this task must not depend on Task 7; instead the test synthesizes its own pyprojects).
- Produces: `check_dist_version.py` derives the expected dist-file prefix by normalizing `[project].name` per PEP 427 (replace each run of `[-_.]` with a single `_`). So `jrag-cli` → prefix `jrag_cli`, and `java-codebase-rag` → prefix `java_codebase_rag`. The filename-version regex is built from that prefix. The METADATA `Version:` equality check is unchanged. The script continues to read the target version from the `--pyproject` file's `[project].version`.

- [ ] **Step 1: Write failing tests**

Extend `tests/package/test_check_dist_version.py`:
1. Keep the existing scenarios but parametrize the package identity: add a helper `_write_pyproject_named(path, name, version)` that writes `[project]\nname = "<name>"\nversion = "<version>"\n`, and a `_write_wheel_named(path, distname, version)` that writes `<distname>-<version>.dist-info/METADATA` inside the zip and names the file `<normalized>-<version>-py3-none-any.whl`.
2. Add `test_jrag_cli_prefix_passes`: pyproject `name="jrag-cli"`, version `0.12.0`; dist has `jrag_cli-0.12.0-py3-none-any.whl` (METADATA `Version: 0.12.0`) + `jrag_cli-0.12.0.tar.gz` → exit 0.
3. Add `test_jrag_cli_prefix_rejects_old_name`: same pyproject, but dist file named `java_codebase_rag-0.12.0-py3-none-any.whl` → exit 1, stderr mentions the foreign/unknown artifact.
4. Add `test_shim_package_prefix_passes`: pyproject `name="java-codebase-rag"`, version `0.12.0`; dist has `java_codebase_rag-0.12.0-*` matching → exit 0. (Confirms the shim's dist passes too.)
5. Update the existing `test_clean_dist_passes` etc. to use `_write_pyproject_named(..., "jrag-cli", ...)` with `jrag_cli-` filenames (or keep `java-codebase-rag` if the test passes that name explicitly — either is fine as long as name and filename agree).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_check_dist_version.py -v`
Expected: FAIL — the `jrag_cli-` cases fail because the hardcoded `java_codebase_rag-` regex rejects them (`filename_version` returns `None`).

- [ ] **Step 3: Implement the generalization**

In `scripts/check_dist_version.py`: add a function `normalized_prefix(name: str) -> str` that lowercases and replaces runs of `[-_.]` with `_` (PEP 427). In `main`, after reading the target version from `--pyproject`, also read `[project].name` and compute the prefix once; build the filename-version regex from that prefix. Replace the hardcoded `java_codebase_rag` literal in `filename_version` (and in the "not a … dist artifact" error text) with the computed prefix. No other behavior changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_check_dist_version.py -v`
Expected: PASS — all scenarios green, both prefixes.

- [ ] **Step 5: Commit**

Run: `git add scripts/check_dist_version.py tests/package/test_check_dist_version.py`
Run: `git commit -m "fix(rename): derive dist-version artifact prefix from project name"`

---

### Task 6: Rebrand user-visible display strings in `src/`

**Files:**
- Modify: various `src/java_codebase_rag/**/*.py` (display strings only)

**Interfaces:**
- Consumes: the global constraints (what NOT to change).
- Produces: every user-visible string in `src/` that brands the tool as "java-codebase-rag" reads "jrag" instead — EXCEPT the exclusion list below.

- [ ] **Step 1: Enumerate occurrences and classify**

Run: `grep -rn "java-codebase-rag" src/ --include='*.py'` and classify each hit into:
  - **KEEP** (do not change): any reference to the on-disk artifact name `.java-codebase-rag` (dir/config/marker/ignore/config_source), any `JAVA_CODEBASE_RAG_*` env-var name, the literal legacy command/alias names `java-codebase-rag` / `java-codebase-rag-mcp` where they appear as invokable commands in help text that must document the alias, and the shim package name reference.
  - **CHANGE** (rebrand to "jrag"): branding in banners, progress labels, error/hint messages, install wizard prompts, and any prose that names "the tool" as `java-codebase-rag` where it is not referring to the on-disk artifact, an env var, or the literal legacy command.

Record the classification list (file:line → KEEP or CHANGE) as a comment in the commit message body or a scratch note; the reviewer needs to see the judgment.

- [ ] **Step 2: Apply the CHANGE edits**

Edit each classified line: replace the branding token with `jrag`. Do not touch KEEP lines. Do not rename any identifier, module, function, or the `java_codebase_rag` package. Do not change on-disk strings or env-var names.

- [ ] **Step 3: Verify no branding leak remains (excluding the KEEP set)**

Run: `grep -rn "java-codebase-rag" src/ --include='*.py'`
Expected: every remaining hit is in the KEEP category (on-disk artifact name, `JAVA_CODEBASE_RAG_*` env var, or a documented legacy alias). Manually confirm none is a branding display string.

Run: `.venv/bin/jrag --help` and `.venv/bin/jrag status --help` (in a repo with an existing index, or accept the no-index advisory) → confirm output brands as `jrag`, with no stray "java-codebase-rag" branding outside intentional alias mentions.

- [ ] **Step 4: Run the package tests touched by display strings**

Run: `.venv/bin/python -m pytest tests/package/test_jrag_status.py tests/package/test_jrag_listing.py tests/package/test_cli_progress_stdout_invariant.py tests/package/test_cli_quiet_parity.py -x`
Expected: PASS. (If a test asserts a specific "java-codebase-rag" display string, update the assertion to "jrag" only where the test is pinning branding, not where it pins an on-disk/env/alias literal. Document each test assertion change.)

- [ ] **Step 5: Commit**

Run: `git add -u src/ tests/`
Run: `git commit -m "docs(rename): rebrand user-visible display strings to jrag"`

---

### Task 7: Create the `java-codebase-rag` shim package + package-shape tests

**Files:**
- Create: `shim/pyproject.toml`
- Create: `shim/README.md`
- Create: `tests/package/test_shim_package.py`

**Interfaces:**
- Consumes: nothing at runtime (the shim has no code).
- Produces: a buildable `java-codebase-rag` shim that, when built, yields a wheel/sdist with NO modules and `requires_dist` containing exactly `jrag-cli==0.12.0`.

- [ ] **Step 1: Write failing tests**

`tests/package/test_shim_package.py` parses `shim/pyproject.toml` with `tomllib` and asserts:
1. `data["project"]["name"] == "java-codebase-rag"`.
2. `data["project"]["version"] == "0.12.0"`.
3. `data["project"]["dependencies"] == ["jrag-cli==0.12.0"]`.
4. There is NO `[project.scripts]` section (`"scripts" not in data["project"]`).
5. There is NO `[tool.setuptools.packages.find]` and NO `[tool.setuptools.packages]` (the shim ships no importable modules).
6. Lockstep guard: the shim version equals the root `pyproject.toml` `[project].version` (read both, assert equal).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/package/test_shim_package.py -v`
Expected: FAIL — `shim/pyproject.toml` does not exist (`FileNotFoundError`).

- [ ] **Step 3: Create the shim**

Create `shim/pyproject.toml` with `[build-system]` (setuptools), `[project]` `name = "java-codebase-rag"`, `version = "0.12.0"`, `description = "Renamed to jrag-cli; this package only depends on jrag-cli. New setups: pip install jrag-cli."`, `requires-python = ">=3.11"`, `license = "MIT"`, `dependencies = ["jrag-cli==0.12.0"]`, and `[project.urls]` pointing at `https://github.com/HumanBean17/jrag`. NO `[project.scripts]`, NO package discovery. Create `shim/README.md` with the same rename notice and `pip install jrag-cli` guidance. (Add `shim/` to `.gitignore` exclusions only if needed — it should be committed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/package/test_shim_package.py -v`
Expected: PASS — all 6 assertions green.

- [ ] **Step 5: Verify the shim builds to an empty wheel**

Run: `cd shim && ../.venv/bin/python -m build --wheel` (or `.venv/bin/python -m build --wheel --no-isolation --outdir dist shim` from repo root if `build` is installed).
Expected: a `java_codebase_rag-0.12.0-py3-none-any.whl` is produced whose only contents are the `.dist-info/` metadata (no `java_codebase_rag/` module tree). Inspect with `unzip -l <wheel>`; assert no `.py` files outside `.dist-info`.

- [ ] **Step 6: Commit**

Run: `git add shim/ tests/package/test_shim_package.py`
Run: `git commit -m "feat(rename): add java-codebase-rag shim package depending on jrag-cli"`

---

### Task 8: Rebrand `README.md`

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the new install name `jrag-cli`, the unified `jrag` command.
- Produces: a README whose headline and body brand the tool as `jrag`; install instructions say `pip install jrag-cli`; all command examples use `jrag …` (operator verbs included, e.g. `jrag install`, `jrag init`); a short "Renamed from java-codebase-rag" callout states that old commands (`java-codebase-rag`, `java-codebase-rag-mcp`) and the old package still work, and that indexes/config/env vars are untouched.

- [ ] **Step 1: Make the edits**

- Change the `# java-codebase-rag` H1 headline to `# jrag`.
- Replace `pip install java-codebase-rag` (every occurrence) with `pip install jrag-cli`.
- Replace command examples `java-codebase-rag <verb>` and `java-codebase-rag-mcp` with `jrag <verb>` and `jrag-mcp` respectively (these are now the canonical commands).
- Add a callout near the top (after the lede) titled "Renamed from java-codebase-rag" explaining: the project was renamed to `jrag`; install with `pip install jrag-cli`; the `java-codebase-rag` / `java-codebase-rag-mcp` commands remain as aliases; existing `pip install -U java-codebase-rag` keeps working (it now pulls in `jrag-cli`); on-disk indexes (`.java-codebase-rag/`), configs (`.java-codebase-rag.yml`), and env vars (`JAVA_CODEBASE_RAG_*`) are unchanged — no re-index or config edit needed.
- Preserve every reference to `.java-codebase-rag` (on-disk) and `JAVA_CODEBASE_RAG_*` (env) verbatim — these are real artifact/env names, not branding.

- [ ] **Step 2: Verify**

Run: `grep -n "java-codebase-rag" README.md`
Expected: remaining hits are ONLY the on-disk artifact name `.java-codebase-rag`, the env-var prefix `JAVA_CODEBASE_RAG_`, the legacy-alias mention in the callout, and the "Renamed from" callout text. No `pip install java-codebase-rag` and no branding use of the old name outside the callout.

Run: `grep -n "pip install" README.md`
Expected: every install instruction reads `pip install jrag-cli` (never `pip install jrag` or `pip install java-codebase-rag`).

- [ ] **Step 3: Commit**

Run: `git add README.md`
Run: `git commit -m "docs(rename): rebrand README to jrag (pip install jrag-cli)"`

---

### Task 9: Rebrand `docs/` and rename `JAVA-CODEBASE-RAG-CLI.md` → `JRAG-CLI.md`

**Files:**
- Rename: `docs/JAVA-CODEBASE-RAG-CLI.md` → `docs/JRAG-CLI.md`
- Modify: `docs/JRAG-CLI.md`, `docs/CONFIGURATION.md`, `docs/AGENT-GUIDE.md`, `docs/EDGE-NAVIGATION.md`, `docs/MANUAL-VERIFICATION-CHECKLIST.md`, `docs/CODEBASE_REQUIREMENTS.md`, `docs/PRODUCT-VISION.md`, `docs/DESIGN.md`, `docs/ARCHITECTURE.md`
- Create: `docs/MIGRATION.md`

**Interfaces:**
- Consumes: the new command/package names.
- Produces: all operator/contributor docs brand the tool as `jrag`, reference `pip install jrag-cli` and `jrag`/`jrag-mcp` commands, and preserve `.java-codebase-rag*` on-disk and `JAVA_CODEBASE_RAG_*` env-var references verbatim. A new `docs/MIGRATION.md` consolidates the old→new map.

- [ ] **Step 1: Rename the CLI doc**

`git mv docs/JAVA-CODEBASE-RAG-CLI.md docs/JRAG-CLI.md`. Update the doc's own title/branding inside to `jrag`.

- [ ] **Step 2: Rebrand each doc**

For each file listed above: replace branding uses of `java-codebase-rag` (the tool/command/package) with `jrag` / `jrag-cli`; replace command examples `java-codebase-rag <verb>` → `jrag <verb>` and `java-codebase-rag-mcp` → `jrag-mcp`; replace `pip install java-codebase-rag` → `pip install jrag-cli`. Preserve verbatim: `.java-codebase-rag*` on-disk names and `JAVA_CODEBASE_RAG_*` env vars.

- [ ] **Step 3: Write `docs/MIGRATION.md`**

A short doc with: (a) the rename summary (brand → `jrag`, package → `jrag-cli`); (b) an old→new command map table (`java-codebase-rag`→`jrag`, `java-codebase-rag-mcp`→`jrag-mcp`); (c) old→new pip name (`java-codebase-rag`→`jrag-cli`, with the note that `pip install -U java-codebase-rag` still works via the shim); (d) the explicit "untouched" list — `.java-codebase-rag/` index, `.java-codebase-rag.yml` config, `.java-codebase-rag.hosts`, `JAVA_CODEBASE_RAG_*` env vars; (e) one line on the deprecation alias behavior.

- [ ] **Step 4: Verify**

Run: `grep -rn "pip install java-codebase-rag" docs/`
Expected: no hits.

Run: `grep -rn "java-codebase-rag" docs/ | grep -vE '\.java-codebase-rag|JAVA_CODEBASE_RAG_|java-codebase-rag-mcp|MIGRATION|renamed|formerly|legacy|alias"`
Expected: empty (every remaining hit is on-disk artifact, env var, the `-mcp` alias, or intentional rename prose). Manually eyeball the leftover hits to confirm.

- [ ] **Step 5: Commit**

Run: `git add docs/`
Run: `git commit -m "docs(rename): rebrand docs to jrag; rename CLI doc; add MIGRATION.md"`

---

### Task 10: Update project `CLAUDE.md` and `mcp.json.example`

**Files:**
- Modify: `CLAUDE.md`
- Modify: `mcp.json.example`

**Interfaces:**
- Consumes: the new names and the renamed CLI doc path.
- Produces: `CLAUDE.md`'s doc map references `docs/JRAG-CLI.md` (not the old filename), CLI references say `jrag`/`jrag-cli`, and it notes the on-disk `.java-codebase-rag*` names are intentionally retained. `mcp.json.example` uses server key `jrag` and command `jrag-mcp`, with `JAVA_CODEBASE_RAG_*` env-var keys unchanged.

- [ ] **Step 1: Edit `CLAUDE.md`**

- In the doc map: replace `docs/JAVA-CODEBASE-RAG-CLI.md` with `docs/JRAG-CLI.md` (operator CLI playbook).
- Update the Python-environment note: `pip install -e ".[dev]"` installs the `jrag-cli` package (the import module remains `java_codebase_rag`).
- Replace branding uses of `java-codebase-rag` (the tool) with `jrag`; keep `.java-codebase-rag` on-disk references and the `tests/*/.java-codebase-rag*` erase instructions verbatim.
- Add one line under the shipped-artifacts or tests section noting that `.java-codebase-rag*` on-disk names are intentionally retained for backward compatibility.

- [ ] **Step 2: Edit `mcp.json.example`**

- Change the server key from `"java-codebase-rag"` to `"jrag"`.
- Change `command` from `"java-codebase-rag-mcp"` to `"jrag-mcp"`.
- Keep the env-var keys `JAVA_CODEBASE_RAG_INDEX_DIR` and `JAVA_CODEBASE_RAG_SOURCE_ROOT` exactly as-is; keep the `.java-codebase-rag` path value verbatim.
- Update the comment block: server key `jrag`, command `jrag-mcp`, and add a one-line note that `java-codebase-rag-mcp` still works as an alias. The walk-up-discovery and Claude Code minimal-config examples also use key `jrag` and command `jrag-mcp`.

- [ ] **Step 3: Verify**

Run: `grep -n "java-codebase-rag" CLAUDE.md`
Expected: remaining hits are on-disk names (`.java-codebase-rag`, `tests/*/.java-codebase-rag*`) or the intentional rename note — no stale tool branding.

Run: `grep -n "java-codebase-rag" mcp.json.example`
Expected: remaining hits (if any) are inside comments noting the legacy alias, plus the `JAVA_CODEBASE_RAG_` env-var keys and the `.java-codebase-rag` path value. The live `"command"` value is `"jrag-mcp"` and the server key is `"jrag"`.

- [ ] **Step 4: Commit**

Run: `git add CLAUDE.md mcp.json.example`
Run: `git commit -m "docs(rename): update CLAUDE.md doc map and mcp.json.example for jrag"`

---

### Task 11: Rebrand consumer artifacts (`skills/`, `agents/`) and sync to `install_data`

**Files:**
- Modify: `skills/explore-codebase/SKILL.md`, `skills/explore-codebase-cli/SKILL.md`, `skills/README.md`, `agents/explorer-rag-cli.md`, `agents/explorer-rag-enhanced.md`
- Sync: `src/java_codebase_rag/install_data/skills/**`, `src/java_codebase_rag/install_data/agents/**`

**Interfaces:**
- Consumes: `scripts/sync_agent_artifacts.py` (the dev→install_data sync).
- Produces: shipped skills/agents brand commands as `jrag`/`jrag-mcp`; `install_data` mirror is in sync.

- [ ] **Step 1: Rebrand the dev-source files**

In the five dev-source files (`skills/explore-codebase/SKILL.md`, `skills/explore-codebase-cli/SKILL.md`, `skills/README.md`, `agents/explorer-rag-cli.md`, `agents/explorer-rag-enhanced.md`): replace command references `java-codebase-rag` → `jrag` and `java-codebase-rag-mcp` → `jrag-mcp`. Preserve `.java-codebase-rag*` on-disk references and `JAVA_CODEBASE_RAG_*` env vars verbatim.

- [ ] **Step 2: Sync into `install_data`**

Run: `.venv/bin/python scripts/sync_agent_artifacts.py`
Expected: copies dev → `src/java_codebase_rag/install_data/` (the four mapped subtrees; `skills/README.md` is intentionally excluded by the script).

- [ ] **Step 3: Verify sync is clean**

Run: `.venv/bin/python scripts/sync_agent_artifacts.py --check`
Expected: exit 0, "All files in sync".

Run: `.venv/bin/python -m pytest tests/package/test_install_data_sync.py -v`
Expected: PASS (the existing sync test should still pass; if it pins a `java-codebase-rag` command string, update it to `jrag`).

- [ ] **Step 4: Verify no stale command branding**

Run: `grep -rn "java-codebase-rag" skills/ agents/ src/java_codebase_rag/install_data/`
Expected: remaining hits are `.java-codebase-rag` on-disk names or `JAVA_CODEBASE_RAG_*` env vars only — no command-branding uses of the old name.

- [ ] **Step 5: Commit**

Run: `git add skills/ agents/ src/java_codebase_rag/install_data/ tests/package/test_install_data_sync.py`
Run: `git commit -m "docs(rename): rebrand skills/agents to jrag and sync install_data"`

---

### Task 12: Full verification — editable reinstall + complete test suite

**Files:**
- None (verification only).

**Interfaces:**
- Consumes: all prior tasks.
- Produces: confidence that the rename is complete and nothing regressed.

- [ ] **Step 1: Clean reinstall**

Run: `rm -rf tests/*/.java-codebase-rag tests/*/.java-codebase-rag.{yml,hosts}` (erase stale manual indexes).
Run: `.venv/bin/pip install -e ".[dev]"`
Expected: clean reinstall of `jrag-cli` 0.12.0.

- [ ] **Step 2: Four console scripts end-to-end**

Run: `.venv/bin/jrag --version` → `jrag 0.12.0 (python …)`.
Run: `.venv/bin/java-codebase-rag --version` → `java-codebase-rag 0.12.0 (python …)` (alias works; no deprecation line because `--version` short-circuits before TTY check is observable — acceptable).
Run (in a TTY-less pipe to confirm silence): `.venv/bin/java-codebase-rag --version 2>&1 | cat` → no deprecation line on stderr.
Run: `.venv/bin/jrag-mcp --help` and `.venv/bin/java-codebase-rag-mcp --help` → both print MCP help, exit 0.
Run: `.venv/bin/jrag --help` → lists BOTH operator verbs (e.g. `install`, `init`) and agent verbs (e.g. `find`, `search`) — confirming unification.

- [ ] **Step 3: Deprecation behavior spot-check**

Run: `.venv/bin/java-codebase-rag --help 2>/tmp/dep.txt; cat /tmp/dep.txt`
Expected: the deprecation line appears on stderr (TTY-gated; in a real terminal). Then `JRAG_NO_DEPRECATION=1 .venv/bin/java-codebase-rag --help 2>/tmp/dep2.txt; cat /tmp/dep2.txt` → no deprecation line.

- [ ] **Step 4: Run the full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: PASS (the full suite is slow; this is the one end-of-task full run per the project rules). If a test fails due to a stale branding assertion, fix the assertion (only where it pins branding, not on-disk/env/alias literals) and re-run.

- [ ] **Step 5: Build both packages and run the guard**

Run: `.venv/bin/python -m build` (root) → produces `dist/jrag_cli-0.12.0-*`.
Run: `.venv/bin/python -m build --outdir shim/dist shim` → produces `shim/dist/java_codebase_rag-0.12.0-*` (the shim).
Run: `.venv/bin/python scripts/check_dist_version.py --dist dist --pyproject pyproject.toml` → exit 0.
Run: `.venv/bin/python scripts/check_dist_version.py --dist shim/dist --pyproject shim/pyproject.toml` → exit 0.

- [ ] **Step 6: Final commit (if any cleanup)**

If Steps 2–5 surfaced stray fixes, commit them: `git add -A && git commit -m "chore(rename): final verification fixes"`. Otherwise no commit.

---

## Rollout (post-merge-of-plan, pre-release) — not implemented in this plan's tasks

Captured for the release operator (the spec's §7.2): publish `jrag-cli` to PyPI first, then the `java-codebase-rag` shim; tag `v0.12.0`; GitHub release notes carry the migration note. These steps are manual and outside the code changes above.
