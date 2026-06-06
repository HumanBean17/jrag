# DIRS-HIERARCHY — Walk-up config discovery and configurable source root

**Status**: proposal — not yet implemented.
**Author**: Dmitry Teryaev
**Date**: 2026-06-06

## TL;DR

- **The call**: add walk-up config discovery (like git) so the tool finds `.java-codebase-rag.yml` in any parent directory, and add a `source_root` field to the YAML config so the config can live separately from the source code.
- **Why**: the tool currently couples three things — config file location, source code location, and cwd. All three must be the same directory. Users who organize projects in varied directory structures hit walls: running `init` from a multi-system parent creates a mixed index, using MCP from a microservice subdirectory can't find the config, and placing the config in a separate context directory requires `--source-root` on every invocation.
- **Scope**: config discovery and source root resolution. Both CLI and MCP server use the same walk-up logic, eliminating the need for env vars in `.mcp.json`. No changes to indexing, query, or graph-building logic. No changes to `init` beyond a warning when a parent config exists.
- **Migration**: 1 PR. No breaking changes. Existing workflows where cwd = config dir continue to work identically. Existing `.mcp.json` files with env vars continue to work (env vars are overrides). New workflows (running from subdirectories, zero-config MCP) unlock.

## 1. Problem statement

Three real user scenarios that break today:

**User A** — multi-system parent directory:
```
IdeaProjects/
  .java-codebase-rag.yml
  System-A/  microservice-A-1/  microservice-A-2/
  System-B/  microservice-B-1/  microservice-B-2/
```
Running `init` from `IdeaProjects/` indexes ALL Java files from all systems into one giant mixed index. The tool doesn't recognize project boundaries.

**User B** — working from a microservice subdirectory:
```
IdeaProjects/
  System-C/
    .java-codebase-rag.yml
    microservice-C-1/
    microservice-C-2/
```
`init` runs correctly from `System-C/`. But then `cd microservice-C-1/` and starting the MCP server — the tool looks for config only in cwd (`microservice-C-1/`), doesn't find it, fails.

**User C** — config lives separately from source code:
```
IdeaProjects/
  System-D/
    system-D-context/
      .java-codebase-rag.yml
    microservice-D-1/
    microservice-D-2/
```
Config is in `system-D-context/`, code is at `../` via `--source-root`. The `--source-root` flag works but must be passed on every invocation. No way to persist this in the config.

**Root cause**: `find_yaml_config_file()` only checks the exact `source_root` directory. No walking up. And the config has no `source_root` field, so the only way to point to code elsewhere is the `--source-root` flag or env var.

## 2. Design principles

1. **cwd independence.** The tool should work from any subdirectory of the project, not just from the directory containing the config.
2. **Config is the anchor.** The presence of `.java-codebase-rag.yml` defines a project boundary. The tool walks up to find it (like git finds `.git`).
3. **Source root is configurable but has a sane default.** Default source root = config file's parent directory. Override via YAML `source_root` field, env var, or CLI flag.
4. **Index follows source root.** The index directory always lives at `<source-root>/.java-codebase-rag/`. Config and source root can be in different places, but the index stays with the code.
5. **No breaking changes.** Existing workflows where cwd = config dir must produce identical behavior. The walk-up is additive — it only fires when the config isn't found in cwd.

## 3. Proposed solution

Once walk-up finds the config file, everything else is derivable — no env vars needed:

```
config found at System-C/.java-codebase-rag.yml
  → source root = System-C/  (or source_root from YAML)
    → index dir = System-C/.java-codebase-rag/
```

Both CLI and MCP server follow the same discovery path. The minimal `.mcp.json` becomes:

```json
{
  "mcpServers": {
    "java-codebase-rag": {
      "type": "stdio",
      "command": "java-codebase-rag-mcp"
    }
  }
}
```

This works because MCP hosts set cwd to the workspace directory at server startup:
- **Claude Code** — cwd = workspace directory. Walk-up from there finds the config.
- **VS Code / Cursor** — cwd = workspace root. Same.
- **Claude Desktop** — cwd is less predictable. Users can still set `JAVA_CODEBASE_RAG_SOURCE_ROOT` as an optional override.

Both `JAVA_CODEBASE_RAG_SOURCE_ROOT` and `JAVA_CODEBASE_RAG_INDEX_DIR` become **optional overrides** rather than requirements.

### 3.1 Walk-up config discovery

New function `discover_project_root(start: Path) -> Path | None` in `config.py`:

- Starts from `start` (typically cwd)
- Checks for `.java-codebase-rag.yml` or `.java-codebase-rag.yaml` in the current directory
- If not found, moves to parent and repeats
- **First match wins** (closest to cwd): if nested configs exist at multiple levels (e.g. `System-A/.java-codebase-rag.yml` and `IdeaProjects/.java-codebase-rag.yml`), the one closest to cwd is used. This mirrors git's behavior when nested `.git` directories exist.
- **Boundary conditions**: stops at `$HOME` (inclusive — checks `$HOME` itself but does not go past it), stops at filesystem root. Rationale: `$HOME` is the natural project root on macOS/Linux workstations. On CI/CD (`/root`, `/home/runner`) this is equally appropriate. Configs above `$HOME` are almost certainly unrelated to the current project.
- Returns the directory containing the config file, or `None`

The function is a pure discovery step — it finds where the config lives, nothing more. It does not parse the config or resolve source roots.

### 3.2 `source_root` field in config YAML

New optional top-level field:

```yaml
# Optional: override where Java source code lives.
# Relative paths resolve relative to the config file's directory.
# Default: the directory containing this config file.
source_root: ../
```

Resolution is straightforward: `Path(config_dir) / source_root`. For the example above, if config is at `system-D-context/.java-codebase-rag.yml`, then `source_root: ../` resolves to `System-D/`.

**Note on resolution base**: the YAML `source_root` field resolves relative to the config file's directory, while the CLI `--source-root` flag resolves relative to cwd. These are intentionally different resolution bases — the YAML field is a portable declaration ("my code is one level up from this config"), while the CLI flag is an absolute or cwd-relative override. The precedence table in §3.3 handles priority; the resolution base difference is a non-issue because each source resolves independently before comparison.

### 3.3 Full precedence chain for source root

| Priority | Source | Example |
|---|---|---|
| 1 (highest) | `--source-root` CLI flag | `--source-root /other/path` |
| 2 | `JAVA_CODEBASE_RAG_SOURCE_ROOT` env var | `export JAVA_CODEBASE_RAG_SOURCE_ROOT=/other/path` |
| 3 | `source_root` field in YAML config | `source_root: ../` |
| 4 | Walk-up discovery result (config file's parent dir) | Config at `System-C/.java-codebase-rag.yml` → source root = `System-C/` |
| 5 (lowest) | `Path.cwd()` (unchanged fallback) | No config found anywhere |

### 3.4 Where changes happen

**`config.py`**:
- Add `discover_project_root(start: Path) -> Path | None`
- Add `find_config_dir(source_root: Path | None) -> Path` — returns the effective project root by combining walk-up discovery with the precedence chain
- Update `resolve_operator_config()` to read `source_root` from YAML and resolve it relative to config dir
- When `source_root` param is `None` (no CLI flag, no env var), the function discovers the project root via walk-up, then reads `source_root` from the discovered YAML, then falls back to cwd

**`server.py`**:
- Update `_project_root()` to call `discover_project_root()` before falling back to cwd. Env var still takes precedence.

**`cli.py`**:
- Update `_resolved_from_ns()` to use walk-up discovery when `--source-root` is not provided. CLI flag still takes precedence.

**`init` command**: no behavior change. The `init` command creates config + index in the specified directory as before. Walk-up only helps find existing configs. Add a soft warning if a parent config is detected.

### 3.5 Error messages

**No config found (MCP/query/index commands)**:
> No `.java-codebase-rag.yml` found in `[cwd]` or any parent directory (stopped at home). Run `java-codebase-rag init` in your project root first.

**`init` finds existing config in parent (soft warning)**:
> Warning: found existing config at `[parent]/.java-codebase-rag.yml`. Creating a new project here will create a separate index.

### 3.6 What each user scenario looks like after

**User A** — runs `init` from each `System-X/` directory separately. Then uses MCP from any subdirectory — walk-up finds the config for the current system. No more mixed indexes.

**User B** — runs `init` from `System-C/`. Then `cd`s to `microservice-C-1/` and starts MCP. Walk-up finds `System-C/.java-codebase-rag.yml`, source root defaults to `System-C/`. Works.

**User C** — creates config at `system-D-context/.java-codebase-rag.yml` with `source_root: ../`. Runs `init` from `system-D-context/`. Walk-up from any subdirectory finds the config. Source root = `System-D/`. Index at `System-D/.java-codebase-rag/`.

## 4. Scope

- Config file discovery via walk-up
- `source_root` field in YAML config
- Updated precedence chain
- Integration in CLI and MCP server
- `JAVA_CODEBASE_RAG_INDEX_DIR` and `JAVA_CODEBASE_RAG_SOURCE_ROOT` env vars become optional (still supported as overrides)
- `mcp.json.example` updated to show minimal zero-env-var config
- Clear error messages when config is not found
- Soft warning during `init` when a parent config exists

## 5. Schema / Ontology / Re-index impact

- Ontology bump: not required
- Re-index required: no. The index structure and content are unchanged.
- Config surface changes: new optional `source_root` field in YAML. Fully backward-compatible — existing configs without this field continue to work identically.

## 6. Tests / Validation

- `test_discover_project_root_finds_config_in_cwd` — config in cwd, returns cwd
- `test_discover_project_root_walks_up` — config in parent, returns parent
- `test_discover_project_root_stops_at_home` — config in $HOME, returns None
- `test_discover_project_root_not_found` — no config anywhere, returns None
- `test_source_root_from_yaml_relative` — `source_root: ../` resolves to parent of config dir
- `test_source_root_from_yaml_absolute` — `source_root: /abs/path` resolves to absolute path
- `test_source_root_precedence_cli_over_yaml` — CLI flag wins over YAML `source_root`
- `test_source_root_precedence_yaml_over_discovery` — YAML `source_root` wins over config dir default
- `test_source_root_precedence_env_over_yaml` — env var wins over YAML `source_root`
- `test_existing_behavior_unchanged` — no walk-up, cwd = config dir → identical behavior to today

## 7. Open questions

None — all key decisions resolved during brainstorming.

## 8. Out of scope

- Auto-detecting multiple systems and splitting indexes
- Changing index directory structure
- Global config or project registry
- Changes to indexing, query, or graph-building logic
- `init` command behavior changes (beyond the parent-config warning)

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Walk-up finds wrong config in a shared parent (e.g. `IdeaProjects/.java-codebase-rag.yml` when user meant `System-A/`) | `init` warns when a parent config exists. First-match-wins means the closest config is always preferred. If a stray config exists at a high level, it's only found when no closer config exists. |
| Symlink cycles during walk-up | `Path.resolve()` canonicalizes the path before walking. The `parent` chain on resolved paths cannot cycle. |
| Performance of filesystem stat calls in deep directory trees | Each step is a single `is_file()` check. Even at 20 levels deep, this is negligible compared to the embedding/indexing work the tool already does. |
| `$HOME` boundary stops too early or too late | `$HOME` is checked inclusively (a config at `$HOME` itself is found). This covers the common macOS case where projects live under `~/Projects/`. Going past `$HOME` would risk picking up system-level or unrelated configs. |
| Nested configs create confusion (which one is active?) | First-match-wins is simple and matches git's behavior. The tool can log which config file it discovered to aid debugging. |

## 10. Decisions taken

1. **First match wins** — closest config to cwd, not "most specific" or "deepest". Matches git behavior. No heuristic for picking among multiple configs.
2. **`$HOME` is inclusive boundary** — check `$HOME` itself, don't go past it. Avoids finding configs in `/` or system directories.
3. **YAML field named `source_root`** — same name as the CLI flag for conceptual consistency, despite different resolution bases. The alternative (`project_root`, `code_dir`) would add a new concept where none is needed.
4. **Walk-up is a separate pre-step** — not integrated into `resolve_operator_config()`. Cleaner separation, easier to test, lower risk to existing resolution logic.
5. **No changes to `init`** — `init` creates config + index as before. The walk-up only helps find existing configs from subdirectories.
6. **No `--walk-up` opt-out flag** — walk-up is always-on when no explicit source root is given. If a user hits the wrong config, the fix is to move or remove the stray config file, not to add a flag.

## 11. Migration plan — 1 PR

Single PR containing:
1. `discover_project_root()` function in `config.py`
2. `source_root` YAML field parsing in `resolve_operator_config()`
3. Updated `_project_root()` in `server.py`
4. Updated `_resolved_from_ns()` in `cli.py`
5. Index dir auto-derived from discovered source root (no env var needed)
6. Soft warning in `init` when parent config detected
7. All tests from §6
8. `mcp.json.example` updated to show minimal zero-env-var config
9. README update documenting the new behavior
