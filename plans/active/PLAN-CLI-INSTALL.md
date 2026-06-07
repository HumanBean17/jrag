# Plan: CLI Install Command

Status: **active (planning)**. This plan implements
[`propose/active/CLI-INSTALL-PROPOSE.md`](../../propose/active/CLI-INSTALL-PROPOSE.md)
as a two-PR sequence. This file is plan-only and does not implement code.

Depends on: none.

## Goal

- A single `java-codebase-rag install` command walks users through a 6-stage interactive pipeline that generates config, deploys MCP registration + skill + agent artifacts, and indexes their project — no manual file editing required.
- `java-codebase-rag update` refreshes shipped artifacts (skill, agent, MCP entry) after `pip install --upgrade` without re-running the full setup wizard.
- Non-interactive mode (`install --non-interactive --agent <host>`) enables CI automation with zero prompts.
- Exit codes (0 success, 1 partial, 2 fatal) and re-run semantics let users safely repeat `install` to fix failed stages.

## Principles (do not relitigate in review)

- **Interactive-first, non-interactive escape hatch.** Every prompt goes through a single `prompt()` helper in `installer.py` that dispatches to `questionary` when `sys.stdin.isatty()` is True, or returns defaults when False. This helper is a first-class abstraction — no ad-hoc `if isatty()` scattered through stage logic.
- **Merge, never overwrite wholesale.** MCP config files (`~/.claude.json`, `.qwen/settings.json`, `.gigacode/settings.json`) are JSON files that contain other keys the installer must not destroy. Always read → merge into `mcpServers` → write back.
- **Shipped artifacts are versioned assets.** Skill (`SKILL.md`) and agent (`explorer-rag-enhanced.md`) files live inside the package as `package_data`. The installer copies them; `update` overwrites them without asking (they are not user-editable).
- **No new Python dependency on `rich`.** The proposal lists `rich` but the codebase already has `cli_format.py` (TTY-aware ANSI styling) and `cli_progress.py`. Use these instead of adding `rich`. Only `questionary` (for interactive prompts) is a new dependency.
- **`source_root` is always cwd.** The installer never asks the user for a source root — it is implicitly cwd. If the user needs a different root, they `cd` first. This matches how `init` works today.
- **No ontology bump, no re-index.** This is a CLI/UX feature. The graph, vector index, and ontology are untouched.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-I1 | `install` subcommand: installer module, prompt helper, host config mapping, artifact deployment, MCP merge, YAML generation, `.gitignore` update, CLI handler, package data wiring, tests | none | `questionary` TTY/non-TTY dispatch; MCP JSON merge correctness; file write permission handling; re-run semantics; `.gitignore` pattern matching | unit: host config, MCP merge, gitignore, prompt helper, YAML gen, artifact deploy + integration: non-interactive install on bank-chat fixture | — |
| PR-I2 | `update` subcommand: host detection, artifact refresh, `--force`/`--dry-run`, graph staleness warning, tests | none | Host detection scanning both project + user paths; stale file detection; `increment` integration | unit: host detection, refresh logic + integration: install-then-update cycle | PR-I1 |

Landing order: **I1 → I2**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Dependency: `rich` | **Not added.** Reuse `cli_format.py` and `cli_progress.py` for progress display. Only `questionary>=2.0` is added. |
| Artifact source location | `java_codebase_rag/install_data/skills/explore-codebase/SKILL.md` and `java_codebase_rag/install_data/agents/explorer-rag-enhanced.md` inside the package. Registered via `package_data` in `pyproject.toml`. |
| MCP entry shape | `{"java-codebase-rag": {"command": "java-codebase-rag-mcp", "type": "stdio"}}` — no env vars (walk-up discovery handles config). Includes `"type": "stdio"` for all hosts (safe default; Claude Code ignores it, Qwen/GigaCode expect it). |
| Interactive prompt library | `questionary` (checkbox for stage 1, select for stages 3/4, text for stage 2, confirm for overwrite prompts). All dispatched through a `prompt()` helper that checks `sys.stdin.isatty()`. |
| Non-interactive mode | `--non-interactive` flag or non-TTY stdin → all defaults, no prompts. Requires `--agent` flag. `--scope project` default. `--model auto` default. |
| Re-run detection | If `.java-codebase-rag.yml` exists, show current values and offer "Update" (pre-filled) or "Start fresh". Unmanaged YAML keys preserved in "Update" mode. |
| Stage 1 detection granularity | Top-level directories only (immediate children of cwd containing `.java` files). Not nested package dirs. |
| `~` expansion | `Path.home()`, not shell expansion. `os.path.expandvars` for `$HOME`. |
| Post-deploy PATH validation | `shutil.which("java-codebase-rag-mcp")` — warn but don't abort if missing (GUI launcher PATH may differ). |
| `.gitignore` pattern-aware check | Check for `.java-codebase-rag` or `.java-codebase-rag/` (with or without trailing slash). Not just string equality. |

---

# PR-I1 — `install` subcommand: interactive setup wizard

## File-by-file changes

### 1. `java_codebase_rag/installer.py` — new module (core installer logic)

This is the primary new file. It contains:

#### 1a-zero. Types

```python
from typing import NamedTuple

Scope = Literal["project", "user"]

class ArtifactResult(NamedTuple):
    path: Path
    success: bool
    error: str | None
```

#### 1a. Host config mapping

A dataclass and mapping table that resolves host identity → directory names + config paths:

```python
@dataclass(frozen=True)
class HostConfig:
    name: str           # "claude-code", "qwen-code", "gigacode"
    dir_name: str       # ".claude", ".qwen", ".gigacode"
    mcp_project: str    # ".mcp.json", ".qwen/settings.json", ".gigacode/settings.json"
    mcp_user: str       # ".claude.json", ".qwen/settings.json", ".gigacode/settings.json"

HOSTS: dict[str, HostConfig] = {
    "claude-code": HostConfig("claude-code", ".claude", ".mcp.json", ".claude.json"),
    "qwen-code":   HostConfig("qwen-code",   ".qwen",  ".qwen/settings.json", ".qwen/settings.json"),
    "gigacode":    HostConfig("gigacode",    ".gigacode", ".gigacode/settings.json", ".gigacode/settings.json"),
}
```

Helper methods on `HostConfig`:
- `scope_path(scope: Literal["project", "user"], cwd: Path) -> Path` — returns `<cwd>/<dir_name>` for project scope, `Path.home()/<dir_name>` for user scope.
- `mcp_config_path(scope: Literal["project", "user"], cwd: Path) -> Path` — returns the full path to the MCP config file.
- `skills_dir(scope, cwd) -> Path` — `<scope_path>/skills/`
- `agents_dir(scope, cwd) -> Path` — `<scope_path>/agents/`

#### 1b. `prompt()` helper

```python
def prompt(prompt_type: str, message: str, *, choices=None, default=None) -> list[str] | str | bool:
    """Interactive prompt that dispatches to questionary on TTY, returns default otherwise."""
```

- When `sys.stdin.isatty()` is True: dispatch to the appropriate `questionary` function (`checkbox`, `select`, `text`, `confirm`) based on `prompt_type`. `import questionary` is lazy — only imported inside this branch.
- When False: return `default` without any interaction.
- `prompt_type` values and return types:
  - `"checkbox"` → `list[str]` (questionary.checkbox returns list of selected values)
  - `"select"` → `str` (questionary.select returns single chosen value)
  - `"text"` → `str` (questionary.text returns entered string)
  - `"confirm"` → `bool` (questionary.confirm returns True/False)
- `KeyboardInterrupt` from questionary is caught and re-raised as `SystemExit(2)` — user Ctrl+C is a clean abort, not a traceback.

#### 1c. Stage 1: Java source detection

```python
def detect_java_directories(cwd: Path) -> list[Path]:
    """Return immediate child directories of cwd that contain .java files recursively."""
```

- Walk cwd immediate children only. For each child that is a directory, check recursively for `.java` files using `any((d / f).is_file() for ...)` or a fast glob.
- Also check if cwd itself contains `.java` files (single-module project).
- If no `.java` found anywhere: raise a fatal error (exit code 2).
- Return list of detected directories (relative to cwd).

#### 1d. Stage 2: Embedding model

```python
def resolve_model(model_input: str | None, *, non_interactive: bool) -> str:
    """Resolve embedding model path or 'auto'."""
```

- If `non_interactive` or `model_input` is None: return `"auto"`.
- If user provides a path: expand `~` and `$HOME`, validate existence.
- If path not found: prompt confirmation via `prompt("confirm", ...)`.
- Return the resolved string.

#### 1e. Stage 3-4: Agent host + scope selection

```python
def select_host(*, non_interactive: bool, cli_agent: str | None) -> HostConfig:
    """Select agent host from menu or CLI flag."""
```

- If `cli_agent` is given: look up in `HOSTS`, error if invalid.
- If non-interactive with no `--agent`: fatal error (exit code 2).
- Interactive: `prompt("select", ...)` with 3 choices.

```python
def select_scope(*, non_interactive: bool, cli_scope: str | None) -> Scope:
    """Select 'project' or 'user' scope."""
```

- Default: `"project"`.
- Interactive: `prompt("select", ...)`.

#### 1f. Stage 5: Artifact deployment

```python
def deploy_artifacts(
    host: HostConfig,
    scope: Scope,
    cwd: Path,
    *,
    non_interactive: bool,
) -> list[ArtifactResult]:
```

For each of 3 artifacts (MCP config, skill, agent):

1. Resolve source path (package data dir or generated JSON) and destination path.
2. Check writability of parent directory. If not writable: record error, skip, continue.
3. Handle existing files:
   - Skill/agent: if exists, prompt overwrite/skip/abort (via `prompt("select", ...)`). Show file size and mtime.
   - MCP config: merge into existing JSON. If `java-codebase-rag` entry already exists with different config, prompt for confirmation.
4. Write the file.
5. Run post-deploy PATH validation for `java-codebase-rag-mcp` via `shutil.which()`.

Return a list of `ArtifactResult` (named tuple with `path`, `success`, `error`).

#### 1g. MCP JSON merge

```python
def merge_mcp_config(config_path: Path, host: HostConfig) -> bool:
    """Read, merge, write MCP config. Returns True if entry was added/updated."""
```

- Read existing JSON (or start with `{}`).
- Ensure `mcpServers` key exists.
- Merge `{"java-codebase-rag": {"command": "java-codebase-rag-mcp", "type": "stdio"}}` into `mcpServers`.
- If entry already exists with same config: no-op, return True.
- If entry exists with different config: update in-place, return True.
- Preserve all other keys in the file (e.g., `~/.claude.json` may have `numStartups`, `userID`).
- Write back atomically (write to `.tmp`, rename).

#### 1h. Stage 6: Index + finish

```python
def generate_yaml_config(
    source_root: Path,
    model: str,
    microservice_roots: list[str] | None,
    existing_yaml: dict | None,
) -> str:
    """Generate .java-codebase-rag.yml content from installer answers."""
```

- Build YAML dict. Keys written by the installer (these are the "managed" keys):
  - `microservice_roots`: written only if user selected a subset of directories (None/empty means "all" and the key is omitted).
  - `embedding.model`: written only if model is not `"auto"` (the default). The value is the resolved model path or hub ID.
- Keys **not written** by the installer:
  - `source_root`: omitted. `config.py` resolves it from the config file's directory (walk-up discovery), so writing it is redundant and could break if the user moves the project.
  - `index_dir`: omitted. `config.py` defaults to `<source_root>/.java-codebase-rag` which is correct for the installer's usage.
  - `embedding.device`: omitted. User can add manually if needed.
  - `hints.enabled`: omitted. Defaults to True in `config.py`.
- If `existing_yaml` is provided (re-run update mode): preserve all keys not in the managed set above (e.g., `brownfield_overrides`, `embedding.device`, custom keys). Overwrite managed keys with new values.
- Return YAML string.

```python
def update_gitignore(cwd: Path) -> None:
    """Add .java-codebase-rag/ to .gitignore if not already present."""
```

- Check if cwd is a git repo (`.git` directory exists). If not: skip silently.
- Check `.gitignore` for `.java-codebase-rag` or `.java-codebase-rag/` (pattern-aware: strip trailing `/` before comparison).
- If not present: append `.java-codebase-rag/` line.
- If `.gitignore` doesn't exist: create it with that single line.

```python
def run_init_if_needed(
    source_root: Path,
    index_dir: Path,
    model: str,
    *,
    non_interactive: bool,
    quiet: bool,
) -> bool:
    """Run init if index directory has no artifacts. Return True if init was run."""
```

- Check `index_dir_has_existing_artifacts(index_dir)`. If occupied: skip init, print "Index already exists. Run `java-codebase-rag reprocess` to rebuild.", return False.
- **Do NOT call `_cmd_init`** — it requires `argparse.Namespace`. Instead, construct a `ResolvedOperatorConfig` directly:
  ```python
  cfg = resolve_operator_config(
      source_root=source_root,
      cli_index_dir=None,      # use default (<source_root>/.java-codebase-rag)
      cli_embedding_model=model if model != "auto" else None,
  )
  cfg.apply_to_os_environ()
  ```
  Then call the underlying pipeline functions from `java_codebase_rag.pipeline`:
  ```python
  from java_codebase_rag.pipeline import run_cocoindex_update, run_build_ast_graph
  from java_codebase_rag.config import index_dir_has_existing_artifacts
  env = cfg.subprocess_env()
  coco = run_cocoindex_update(env, full_reprocess=False, quiet=quiet)
  # ... handle coco.returncode ...
  g = run_build_ast_graph(source_root=cfg.source_root, kuzu_path=cfg.kuzu_path, env=env)
  # ... handle g.returncode ...
  ```
- Return True if init was run, False if skipped.

#### 1i. Re-run detection

```python
def handle_rerun(cwd: Path, *, non_interactive: bool) -> dict | None:
    """If .java-codebase-rag.yml exists, offer update/fresh-start. Return existing YAML data or None."""
```

- If config file exists: read it.
- Interactive: show current values, prompt "Update" or "Start fresh" or "Abort".
- "Update": return parsed YAML for pre-filling.
- "Start fresh": confirm, return None.
- Non-interactive: default to "Update" mode, return parsed YAML.

#### 1j. Orchestrator

```python
def run_install(
    *,
    non_interactive: bool,
    agent: str | None,
    scope: str | None,
    model: str | None,
    cwd: Path | None = None,
) -> int:
    """Run the 6-stage install pipeline. Returns exit code."""
```

This is the top-level function called from `_cmd_install` in `cli.py`. It orchestrates:
1. Detect Java sources (fatal exit 2 if none).
2. Resolve model.
3. Select host.
4. Select scope.
5. Deploy artifacts (partial failure → exit 1).
6. Generate YAML, update `.gitignore`, run `init` if needed.
7. Print summary.

### 2. `java_codebase_rag/install_data/` — new package data directory

Create directory structure:

```
java_codebase_rag/install_data/
├── __init__.py          (empty)
├── skills/
│   └── explore-codebase/
│       └── SKILL.md     (copy from skills/explore-codebase/SKILL.md)
└── agents/
    └── explorer-rag-enhanced.md  (copy from agents/explorer-rag-enhanced.md)
```

The build process copies from repo-root `skills/` and `agents/` at build time. For development, these files are symlinks or copied manually.

**Important:** The `install_data` directory must be importable via `importlib.resources` so the installed package can find its assets regardless of installation method (pip, editable, wheel).

The `installer.py` module reads package data via this helper:

```python
from importlib.resources import files

_PACKAGE_DATA = files("java_codebase_rag.install_data")

def _read_package_artifact(relative_path: str) -> str:
    """Read a shipped artifact from package data. Returns UTF-8 text."""
    return _PACKAGE_DATA.joinpath(relative_path).read_text(encoding="utf-8")
```

Usage in `deploy_artifacts`:
- Skill: `_read_package_artifact("skills/explore-codebase/SKILL.md")`
- Agent: `_read_package_artifact("agents/explorer-rag-enhanced.md")`

### 3. `pyproject.toml` — dependency + package data changes

Add `questionary` to `dependencies`:
```toml
"questionary>=2.0,<3",
```

Add `package_data` section:
```toml
[tool.setuptools.package-data]
"java_codebase_rag" = ["install_data/skills/**/*", "install_data/agents/**/*"]
```

**Do NOT add `rich`** — the proposal mentions it but `cli_format.py` already provides TTY-aware formatting.

### 4. `java_codebase_rag/cli.py` — new subcommand handler

Add `_cmd_install` function and wire it into `build_parser()`:

```python
def _cmd_install(args: argparse.Namespace) -> int:
    from java_codebase_rag.installer import run_install
    return run_install(
        non_interactive=bool(args.non_interactive),
        agent=args.agent,
        scope=args.scope,
        model=args.model,
    )
```

Add `install` subparser:
```python
install = subparsers.add_parser(
    "install",
    help="Interactive setup wizard: config, MCP registration, skill/agent deployment, indexing.",
    description="(...)"
)
install.add_argument("--non-interactive", action="store_true")
install.add_argument("--agent", choices=["claude-code", "qwen-code", "gigacode"], default=None)
install.add_argument("--scope", choices=["project", "user"], default=None)
install.add_argument("--model", type=str, default=None)
install.set_defaults(handler=_cmd_install)
```

Position `install` in the subparser list after `init` (lifecycle group).

### 5. `java_codebase_rag/__init__.py` — ensure package data is accessible

The existing `__init__.py` must remain compatible with `importlib.resources`. No change needed if it's empty or minimal. If it defines `__all__`, add the new module.

### 6. `tests/test_installer.py` — new test module

All tests for PR-I1. See Tests section below for named test cases.

## Tests for PR-I1

### Unit tests

1. `test_host_config_paths_claude_code_project` — HostConfig for claude-code + project scope resolves `.claude/skills/`, `.claude/agents/`, `.mcp.json`
2. `test_host_config_paths_claude_code_user` — HostConfig for claude-code + user scope resolves `~/.claude/skills/`, `~/.claude/agents/`, `~/.claude.json`
3. `test_host_config_paths_qwen_project` — Qwen Code + project: `.qwen/skills/`, `.qwen/agents/`, `.qwen/settings.json`
4. `test_host_config_paths_qwen_user` — Qwen Code + user: `~/.qwen/skills/`, `~/.qwen/agents/`, `~/.qwen/settings.json`
5. `test_host_config_paths_gigacode_project` — GigaCode + project
6. `test_host_config_paths_gigacode_user` — GigaCode + user
7. `test_yaml_generation_auto_model` — model=auto → YAML has no `embedding.model` key and no `source_root` key
8. `test_yaml_generation_custom_model` — model=/path/to/model → YAML has `embedding.model: /path/to/model` but no `source_root`
9. `test_yaml_generation_with_microservice_roots` — subset of dirs → YAML has `microservice_roots: [service-a, service-b]`
10. `test_yaml_generation_all_dirs_selected` — all dirs → no `microservice_roots` in YAML
11. `test_yaml_generation_preserves_unmanaged_keys` — existing YAML with `brownfield_overrides` and `embedding.device` → both preserved in update mode
12. `test_yaml_generation_does_not_write_source_root_or_index_dir` — generated YAML never contains `source_root` or `index_dir` keys
12. `test_mcp_merge_adds_to_empty` — empty `{}` → `{"mcpServers": {"java-codebase-rag": {"command": "java-codebase-rag-mcp", "type": "stdio"}}}`
13. `test_mcp_merge_adds_to_existing_servers` — existing `{"mcpServers": {"other": {...}}}` → both servers present
14. `test_mcp_merge_updates_existing_entry` — existing `java-codebase-rag` entry with different command → updated
15. `test_mcp_merge_preserves_other_keys_claude_json` — `{"numStartups": 42, "userID": "abc", "mcpServers": {...}}` → `numStartups` and `userID` preserved
16. `test_mcp_merge_preserves_other_keys_settings_json` — `{"security": {...}, "$version": 2, "mcpServers": {...}}` → preserved
17. `test_gitignore_creates_if_missing` — no `.gitignore` → created with `.java-codebase-rag/`
18. `test_gitignore_appends_if_not_present` — existing `.gitignore` without pattern → appended
19. `test_gitignore_skips_if_present_with_slash` — existing `.java-codebase-rag/` → no change
20. `test_gitignore_skips_if_present_without_slash` — existing `.java-codebase-rag` → no change
21. `test_gitignore_skips_if_not_git_repo` — no `.git` dir → no file created, no error
22. `test_prompt_dispatches_to_questionary_on_tty` — mock `sys.stdin.isatty()` → questionary called
23. `test_prompt_returns_default_on_non_tty` — non-TTY → default returned, questionary not called
24. `test_rerun_detects_existing_config` — existing `.java-codebase-rag.yml` → returns parsed data
25. `test_rerun_no_config_returns_none` — no config → returns None
26. `test_detect_java_no_files_exit_code_2` — cwd with no `.java` files → raises SystemExit or returns 2
27. `test_detect_java_single_module` — cwd with `src/main/java/Foo.java` → returns cwd as sole entry
28. `test_detect_java_multi_module` — cwd with `service-a/src/.../Foo.java` and `service-b/src/.../Bar.java` → returns both dirs
29. `test_model_path_not_found_prompts_confirmation` — non-existent path → confirmation prompt
30. `test_model_path_found_returns_resolved` — existing path → returned expanded
31. `test_path_validation_warns_missing_mcp_entrypoint` — `shutil.which` returns None → warning printed, continues
32. `test_permission_error_skips_artifact_continues` — unwritable directory → artifact skipped, others continue, exit 1
33. `test_select_host_non_interactive_requires_agent` — no `--agent` in non-interactive → exit 2
34. `test_select_host_invalid_agent_exit_2` — unknown agent string → exit 2
35. `test_artifact_overwrite_prompt_existing_skill` — existing skill file → prompts overwrite/skip/abort

### Integration test

36. `test_install_non_interactive_claude_code_bank_chat` — run `install --non-interactive --agent claude-code` from `tests/bank-chat-system/` fixture. This test is gated behind `JAVA_CODEBASE_RAG_RUN_HEAVY=1` (same as other e2e tests — see `tests/README.md`). It calls `run_install()` directly (not via subprocess) with mocked pipeline functions (`run_cocoindex_update`, `run_build_ast_graph` are mocked to return success `CompletedProcess`). Verify:
    - `.java-codebase-rag.yml` created (no `source_root` key, no `embedding.model` if auto)
    - `.mcp.json` has `java-codebase-rag` entry with `"command": "java-codebase-rag-mcp", "type": "stdio"`
    - `.claude/skills/explore-codebase/SKILL.md` exists
    - `.claude/agents/explorer-rag-enhanced.md` exists
    - `.gitignore` has `.java-codebase-rag/`
    - `run_install()` returns 0

## Definition of done (PR-I1)

- `install` subcommand is registered and appears in `--help` output.
- `install --non-interactive --agent claude-code` completes end-to-end on bank-chat fixture with exit code 0.
- All 37 named tests pass.
- `ruff check .` is clean.
- Existing test suite (`pytest tests -v` without `JAVA_CODEBASE_RAG_RUN_HEAVY`) passes.
- `questionary` is listed in `pyproject.toml` dependencies.
- Package data (skill, agent files) is accessible via `importlib.resources` in both editable and wheel installs.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | --- |
| 1 | Create `java_codebase_rag/install_data/` directory with `__init__.py`, copy skill and agent files | `install_data/` | `importlib.resources` can find both files from a test |
| 2 | Add `package_data` to `pyproject.toml`; add `questionary` dependency | `pyproject.toml` | `pip install -e .` succeeds and `import questionary` works |
| 3 | Implement `HostConfig` dataclass + `HOSTS` mapping + scope/path helpers | `installer.py` | Tests 1-6 pass |
| 4 | Implement `prompt()` helper | `installer.py` | Tests 22-23 pass |
| 5 | Implement `detect_java_directories` (stage 1) | `installer.py` | Tests 26-28 pass |
| 6 | Implement `resolve_model` (stage 2) | `installer.py` | Tests 29-30 pass |
| 7 | Implement `select_host` + `select_scope` (stages 3-4) | `installer.py` | Tests 33-34 pass |
| 8 | Implement `merge_mcp_config` | `installer.py` | Tests 12-16 pass |
| 9 | Implement `deploy_artifacts` (stage 5) | `installer.py` | Tests 31-32, 35 pass |
| 10 | Implement `generate_yaml_config` + `update_gitignore` + `run_init_if_needed` (stage 6) | `installer.py` | Tests 7-11, 17-21 pass |
| 11 | Implement `handle_rerun` | `installer.py` | Tests 24-25 pass |
| 12 | Implement `run_install` orchestrator | `installer.py` | Integration test 36 passes |
| 13 | Add `_cmd_install` + `install` subparser to `cli.py` | `cli.py` | `java-codebase-rag install --help` prints usage |
| 14 | Final validation: full test suite + ruff | all | Green suite, clean lint |

---

# PR-I2 — `update` subcommand: post-upgrade refresh

## File-by-file changes

### 1. `java_codebase_rag/installer.py` — add update logic

#### 1a. Host detection

```python
def detect_configured_hosts(cwd: Path) -> list[tuple[HostConfig, str]]:
    """Scan project + user config files for java-codebase-rag MCP entries.
    Returns list of (host_config, scope) tuples."""
```

- Scan project-level: `.mcp.json`, `.qwen/settings.json`, `.gigacode/settings.json`
- Scan user-level: `~/.claude.json`, `~/.qwen/settings.json`, `~/.gigacode/settings.json`
- For each file that exists, parse JSON, check if `mcpServers.java-codebase-rag` exists.
- Return matching `(HostConfig, scope)` pairs.

#### 1b. Artifact refresh

```python
def refresh_artifacts(
    host: HostConfig,
    scope: str,
    cwd: Path,
    *,
    force: bool,
    dry_run: bool,
) -> list[ArtifactResult]:
    """Overwrite skill and agent files from package data. Skip MCP if entry is correct."""
```

- For skill and agent files: compare content with package data. If different (or `--force`): overwrite. If `--dry-run`: print what would change, don't write.
- For MCP config: if entry exists and matches `{"command": "java-codebase-rag-mcp", "type": "stdio"}`, skip. If different or missing: merge.

#### 1c. `run_update` orchestrator

```python
def run_update(
    *,
    force: bool,
    dry_run: bool,
    cwd: Path | None = None,
) -> int:
    """Run the update pipeline. Returns exit code."""
```

1. Detect configured hosts.
2. If none found: print "No configured agent hosts found. Run `java-codebase-rag install` first." and exit code 2.
3. For each host: refresh artifacts.
4. Check if an index exists (look for `.java-codebase-rag.yml` in cwd via walk-up discovery, then check `index_dir_has_existing_artifacts`). If no index: skip `increment`, print "No index found. Run `java-codebase-rag install` to create one." If index exists: run `increment` on the index (LanceDB catch-up). Print graph staleness warning (same as `_INCREMENT_WARNING_LINES` from `cli.py`).
5. Print summary of what was refreshed.

### 2. `java_codebase_rag/cli.py` — add `update` subcommand

```python
def _cmd_update(args: argparse.Namespace) -> int:
    from java_codebase_rag.installer import run_update
    return run_update(
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )
```

Add `update` subparser with `--force` and `--dry-run` flags.

### 3. `tests/test_installer.py` — add update tests

## Tests for PR-I2

1. `test_detect_hosts_project_mcp_json` — `.mcp.json` with entry → detects claude-code project scope
2. `test_detect_hosts_user_claude_json` — `~/.claude.json` with entry → detects claude-code user scope
3. `test_detect_hosts_multiple_hosts` — both `.mcp.json` and `.qwen/settings.json` → returns both
4. `test_detect_hosts_no_config_returns_empty` — no MCP configs → empty list
5. `test_detect_hosts_ignores_unrelated_entries` — `mcpServers` with other tools but not `java-codebase-rag` → empty
6. `test_refresh_skill_overwrites_stale` — skill file differs from package → overwritten
7. `test_refresh_skill_skips_if_matching` — skill file matches → not overwritten (unless `--force`)
8. `test_refresh_mcp_skips_if_correct` — MCP entry matches → not modified
9. `test_refresh_dry_run_prints_no_write` — `--dry-run` → prints changes, no files written
10. `test_update_no_hosts_exit_2` — no configured hosts → exit 2
11. `test_update_no_index_skips_increment` — hosts configured but no index directory → `increment` skipped, warning printed
12. `test_install_then_update_cycle` — install then update: artifacts refreshed, no errors

## Definition of done (PR-I2)

- `update` subcommand is registered and appears in `--help`.
- `update` after `install` completes with exit 0.
- `update --dry-run` reports what would change without writing.
- `update` with no configured hosts exits with code 2.
- All 12 named tests pass.
- Full test suite passes.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | --- |
| 1 | Implement `detect_configured_hosts` | `installer.py` | Tests 1-5 pass |
| 2 | Implement `refresh_artifacts` | `installer.py` | Tests 6-9 pass |
| 3 | Implement `run_update` orchestrator | `installer.py` | Test 10 passes |
| 4 | Add `_cmd_update` + `update` subparser to `cli.py` | `cli.py` | `java-codebase-rag update --help` prints usage |
| 5 | Integration test: install-then-update cycle | `test_installer.py` | Test 11 passes |
| 6 | Final validation | all | Green suite, clean lint |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | `questionary` import fails in non-interactive environments (no `prompt_toolkit`) | medium | `prompt()` helper checks `isatty()` before importing questionary; non-interactive mode never calls questionary functions. Consider lazy import: `import questionary` only inside the TTY branch. |
| 2 | MCP config file format varies across agent host versions | medium | Merge logic is defensive: read JSON, ensure `mcpServers` key, merge entry. If JSON parse fails, print error and skip that artifact (exit 1). |
| 3 | Package data not found in wheel vs editable install | medium | Use `importlib.resources.files("java_codebase_rag.install_data")` which works in both modes. Test with `pip install -e .` and verify `importlib.resources` path resolves. |
| 4 | Re-run "Update" mode corrupts YAML with hand-edited keys | low | `generate_yaml_config` reads existing YAML, preserves keys not managed by installer, only overwrites installer-managed keys. Test explicitly (`test_yaml_generation_preserves_unmanaged_keys`). |
| 5 | `shutil.which("java-codebase-rag-mcp")` fails in GUI-launched terminals with different PATH | low | Print warning only, don't abort. The entrypoint may work from the agent host's process even if not found from the installer's shell. |

# Out of scope

- Claude Desktop config editing (different location, different format — future work)
- IDE-specific integrations (VS Code, JetBrains extensions)
- Docker / containerized setup
- Python version checking or virtual environment creation
- Automatic `pip install`
- `microservice_roots` advanced configuration (basic top-level detection only; manual YAML editing for complex multi-service layouts)
- `reprocess` / `erase` orchestration (user runs these manually)
- Adding `rich` dependency (reuse `cli_format.py`)
- Changes to MCP tools, graph schema, or vector index

# Whole-plan done definition

1. `java-codebase-rag install --non-interactive --agent claude-code` completes on bank-chat fixture with exit code 0, producing valid config, MCP registration, skill, agent, and index.
2. `java-codebase-rag install` (interactive) can be run on a real Java project and completes the 6-stage wizard.
3. `java-codebase-rag update` after install refreshes artifacts with exit code 0.
4. All named tests pass.
5. `ruff check .` is clean and existing test suite passes.
6. No ontology bump or re-index required for existing installations.

# Tracking

- `PR-I1`: _pending_
- `PR-I2`: _pending_
