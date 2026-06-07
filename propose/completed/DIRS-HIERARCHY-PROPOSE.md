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

**User B+** — microservice scope leakage (NEW):
After walk-up is implemented, `cd microservice-C-1/` and starting MCP works — the config is found at `System-C/`. However, queries return results from ALL microservices (`microservice-C-1/`, `microservice-C-2/`, etc.) because the index spans the entire system. An agent working inside `microservice-C-1/` sees code from `microservice-C-2/`, which can mislead it about the codebase boundaries and cause incorrect conclusions.

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
6. **Microservice scope should match working context.** When an agent works inside a microservice directory, queries should automatically scope to that microservice. No manual filter required.

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

**Traversal algorithm:**

1. **Initialize**: Set `current = start.resolve()` (canonicalize via `Path.resolve()` to handle symlinks)
2. **Check home boundary**: Get `home = Path.home()`. If `home` cannot be resolved, log warning and use filesystem root `/` as boundary
3. **Loop**: While `current` exists and is not past boundary:
   a. Check for config files in order: `.java-codebase-rag.yml`, then `.java-codebase-rag.yaml`
   b. **If both exist in same directory**: Prefer `.yml` over `.yaml` (establish precedence order)
   c. **If config found**: Return `current` (the directory containing the config, not the config file itself)
   d. **If `current == home`**: Break (check home itself, then stop — inclusive boundary)
   e. **If `current.parent == current`**: Break (reached filesystem root)
   f. **Move to parent**: `current = current.parent`
4. **Return None**: No config found

**Error handling:**

- **Permission denied on directory**: Log warning at WARNING level, continue to parent
- **Home directory inaccessible**: Log warning, fall back to filesystem root boundary
- **Config file exists but unreadable**: Log error, continue as if not found (same as missing)

**Stopping conditions:**

The walk-up stops when any of these conditions is met:
- Config file is found and successfully read
- Current directory equals home directory (after checking it)
- Current directory has no parent (filesystem root reached)

**First match wins** (closest to cwd): if nested configs exist at multiple levels (e.g. `System-A/.java-codebase-rag.yml` and `IdeaProjects/.java-codebase-rag.yml`), the one closest to cwd is used. This mirrors git's behavior when nested `.git` directories exist.

**Boundary rationale**: The home directory is the natural project root on user workstations. On CI/CD (`/root`, `/home/runner`) this is equally appropriate. Configs above home are almost certainly unrelated to the current project. Use `Path.home()` for cross-platform compatibility (returns `$HOME` on Unix/macOS, `%USERPROFILE%` on Windows).

**Returns**: The directory containing the config file, or `None` if no config found.

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

### 3.4 Path resolution base differences

**Important**: The YAML `source_root` field and the CLI `--source-root` flag resolve relative paths from different bases:

| Source | Resolution base | Example `../` from `services/api/` |
|---|---|---|
| YAML `source_root: ../` | Config file's directory | If config is at `System-C/.java-codebase-rag.yml`, resolves to `System-C/../` (parent of System-C) |
| CLI `--source-root ../` | Current working directory | If cwd is `services/api/`, resolves to `services/` |
| Env var `JAVA_CODEBASE_RAG_SOURCE_ROOT=../` | Current working directory | If cwd is `services/api/`, resolves to `services/` |

**How precedence interacts with resolution bases:**

Each source in the precedence chain resolves independently using its own resolution base:

1. **CLI flag**: Resolves relative to cwd at invocation time
2. **Env var**: Resolves relative to cwd at server startup/CLI invocation
3. **YAML field**: Resolves relative to config file's directory (discovered via walk-up)
4. **Walk-up result**: Already an absolute path (the config directory)
5. **cwd fallback**: Current working directory at time of resolution

**Key point**: The precedence chain selects ONE source, then that source is resolved. The different resolution bases do not interact with each other — they apply at different stages of selection and resolution. For example, if a YAML `source_root: ../` is selected, it resolves relative to the config dir, regardless of what cwd might be.

**Why this design works:**
- **YAML field** is a portable declaration tied to the config file's location ("my code is one level up from this config")
- **CLI flag** is a runtime override relative to where the command is executed
- **Env var** follows CLI convention (cwd-relative) for consistency

Example showing the difference:
```
# Directory structure
System-C/
  .java-codebase-rag.yml    # Contains: source_root: src/
  services/
    api/                     # Cwd is here

# YAML source_root resolves to:
System-C/src/

# CLI --source-root ../ resolves to (from System-C/services/api/):
System-C/services/
```

### 3.5 Where changes happen

**`config.py`**:
- Add `discover_project_root(start: Path) -> Path | None`
- Add `find_config_dir(source_root: Path | None) -> Path` — returns the effective project root by combining walk-up discovery with the precedence chain
- Update `resolve_operator_config()` to read `source_root` from YAML and resolve it relative to config dir
- When `source_root` param is `None` (no CLI flag, no env var), the function discovers the project root via walk-up, then reads `source_root` from the discovered YAML, then falls back to cwd

**`server.py`**:
- Update `_project_root()` to call `discover_project_root()` before falling back to cwd. Env var still takes precedence.
- Update `_resolve_lancedb_uri()` to use `_project_root()` instead of raw `Path.cwd()` when `JAVA_CODEBASE_RAG_INDEX_DIR` is unset. This ensures consistency: index dir and source root derive from the same discovered location.
- **When `JAVA_CODEBASE_RAG_INDEX_DIR` is set but `JAVA_CODEBASE_RAG_SOURCE_ROOT` is not**: The index dir uses the env var value (absolute path or resolved relative to cwd), while source_root uses walk-up discovery. This is intentional — the index dir env var is an explicit override for where the index lives, independent of source root discovery.

**`cli.py`**:
- Update `_resolved_from_ns()` to use walk-up discovery when `--source-root` is not provided. CLI flag still takes precedence.

**`init` command**: no behavior change. The `init` command creates config + index in the specified directory as before. Walk-up only helps find existing configs. Add a soft warning if a parent config is detected.

### 3.8 Microservice auto-scope

**Problem:** When working from a microservice subdirectory, queries return results from the entire system index, which includes all microservices. This can mislead agents by showing code outside their current context.

**Solution:** Automatically detect the current microservice from cwd and apply it as a filter to all queries.

**Detection logic (Option A - reuse indexing logic):**

Microservice detection uses the same logic as indexing, with an important addition for the source_root level case:

1. **Check if cwd equals source_root**: If `cwd.resolve() == source_root.resolve()`, return `None` (at system level, no specific scope). This is NEW behavior specific to auto-scope — it ensures that working at the project root shows all microservices rather than arbitrarily scoping to one.
2. **Check if cwd outside source_root**: If cwd is not under source_root, return `None` (outside project context)
3. **Walk up to find outermost build marker**: From cwd, walk up to find the outermost build marker (pom.xml, build.gradle, etc.) under source_root
4. **Resolve to microservice name**: Use `microservice_for_path()` from `graph_enrich.py` with the detected build marker path
5. **Check YAML overrides**: Apply `microservice_roots` YAML config if present

**Why the source_root check is needed:**

During indexing, `microservice_for_path()` returns the first path segment when no build marker is found. This is appropriate for indexing (every file belongs to some microservice). But for query-time scoping, working at the project root should show ALL microservices, not arbitrarily scope to the first one. The source_root equality check implements this semantic difference.

**Scope behavior (Option B - scoped inside, all at root):**

- When inside a microservice directory → auto-scope queries to that microservice
- When at source_root level → queries span all microservices (with advisory message)
- Explicit `filter={"microservice": "..."}` always overrides auto-detected scope

**Implementation (Approach 1 - server-level scope injection):**

The MCP server detects microservice at startup and caches it as "default_scope". For each query:

1. Check if user provided explicit `microservice` filter
2. If yes → use user's filter (explicit wins)
3. If no → inject auto-detected scope

**Components:**

1. **`detect_microservice_from_path(cwd, source_root)`** in `graph_enrich.py`
   - Returns microservice name or None (at source_root level)
   - Reuses existing `microservice_for_path()` logic

2. **`ScopeManager` class** in `server.py`
   - Initialized after source_root resolution
   - Detects and caches default_scope at server startup
   - Logs detection at INFO level
   - `apply_auto_scope(filter)` method injects scope when needed
   - **Scope lifecycle**: Scope is detected once at server startup and cached for the server's lifetime. If the user changes directories during a long-running MCP session, the scope will NOT be re-detected automatically. Users who change directories should restart the MCP server to get updated scope detection. This is a known limitation documented in README.

3. **Tool wrapper integration** in `server.py`
   - Each MCP tool wrapper calls `apply_auto_scope()` before passing filter to underlying function

**Error handling:**

| Scenario | Behavior |
|----------|----------|
| source_root cannot be resolved | Log warning, continue with no auto-scope |
| microservice detection fails | Log warning, continue with no auto-scope |
| cwd outside source_root | No scope applied (None) |
| User provides invalid microservice | Existing validation catches it |

**Logging and advisories:**

- INFO-level logging shows detected microservice at startup
- Exact log messages:
  - When scope detected: `[scope] Detected microservice: {microservice_name}` then `[scope] Queries scoped to {microservice_name}`
  - When no scope (at source_root): `[scope] No microservice detected (at project root)` then `[scope] Queries will span all microservices`
- Advisory message (shown in MCP response advisories field when at source_root and no explicit microservice filter): `Query results span multiple microservices. Use filter='{"microservice": "..."}' to scope to a specific service.`

### 3.6 Error messages

**No config found (MCP/query/index commands)**:
> No `.java-codebase-rag.yml` found in `[cwd]` or any parent directory (stopped at home). Run `java-codebase-rag init` in your project root first.

**`init` finds existing config in parent (soft warning)**:
> Warning: found existing config at `[parent]/.java-codebase-rag.yml`. Creating a new project here will create a separate index.

### 3.7 What each user scenario looks like after

**User A** — runs `init` from each `System-X/` directory separately. Then uses MCP from any subdirectory — walk-up finds the config for the current system. No more mixed indexes.

**User B** — runs `init` from `System-C/`. Then `cd`s to `microservice-C-1/` and starts MCP. Walk-up finds `System-C/.java-codebase-rag.yml`, source root defaults to `System-C/`. Works.

**User C** — creates config at `system-D-context/.java-codebase-rag.yml` with `source_root: ../`. Runs `init` from `system-D-context/`. Walk-up from any subdirectory finds the config. Source root = `System-D/`. Index at `System-D/.java-codebase-rag/`.

**User B+** — runs `init` from `System-C/`. Then `cd`s to `microservice-C-1/` and starts MCP. Walk-up finds `System-C/.java-codebase-rag.yml`, source root defaults to `System-C/`. Microservice auto-scope detects `microservice-C-1` from cwd and applies it automatically. Queries return only results from `microservice-C-1`. Agent sees correct codebase boundaries.

## 4. Scope

- Config file discovery via walk-up
- `source_root` field in YAML config
- Updated precedence chain
- Integration in CLI and MCP server
- `JAVA_CODEBASE_RAG_INDEX_DIR` and `JAVA_CODEBASE_RAG_SOURCE_ROOT` env vars become optional (still supported as overrides)
- `mcp.json.example` updated to show minimal zero-env-var config
- Clear error messages when config is not found
- Soft warning during `init` when a parent config exists
- Logging of discovered config file path at INFO level (for debugging discovery issues)
- Optional `--debug` / `--verbose` flag that prints the full discovery path and resolution chain
- README documentation of YAML vs CLI path resolution base differences with examples
- **NEW:** Microservice auto-scope detection and application
- **NEW:** `ScopeManager` class in server.py
- **NEW:** `detect_microservice_from_path()` function in graph_enrich.py
- **NEW:** Advisory messages when queries span multiple microservices

## 5. Schema / Ontology / Re-index impact

- Ontology bump: not required
- Re-index required: no. The index structure and content are unchanged.
- Config surface changes: new optional `source_root` field in YAML. Fully backward-compatible — existing configs without this field continue to work identically.

## 6. Tests / Validation

- `test_discover_project_root_finds_config_in_cwd` — config in cwd, returns cwd
- `test_discover_project_root_walks_up` — config in parent, returns parent
- `test_discover_project_root_stops_at_home` — config in $HOME, returns None
- `test_discover_project_root_stops_at_windows_userprofile` — config in %USERPROFILE%, returns None (Windows-specific)
- `test_discover_project_root_not_found` — no config anywhere, returns None
- `test_discover_project_root_cross_platform_home` — `Path.home()` correctly identifies home on both Unix and Windows
- `test_source_root_from_yaml_relative` — `source_root: ../` resolves to parent of config dir
- `test_source_root_from_yaml_absolute` — `source_root: /abs/path` resolves to absolute path
- `test_source_root_precedence_cli_over_yaml` — CLI flag wins over YAML `source_root`
- `test_source_root_precedence_yaml_over_discovery` — YAML `source_root` wins over config dir default
- `test_source_root_precedence_env_over_yaml` — env var wins over YAML `source_root`
- `test_existing_behavior_unchanged` — no walk-up, cwd = config dir → identical behavior to today
- `test_discover_project_root_with_symlinks` — symlinked config dirs are handled correctly
- `test_yaml_relative_path_resolution_base` — YAML `source_root: ../` resolves relative to config dir, not cwd
- `test_cli_flag_resolution_base` — `--source-root ../` resolves relative to cwd, not config dir
- **NEW:** `test_detect_microservice_deep_inside` — Deep inside microservice directory detects that microservice
- **NEW:** `test_detect_microservice_at_microservice_root` — At microservice root detects that microservice
- **NEW:** `test_detect_microservice_at_system_root` — At system root returns None (no specific scope)
- **NEW:** `test_detect_microservice_outside_source` — Outside source_root returns None
- **NEW:** `test_apply_scope_when_filter_none` — No filter provided injects auto-detected scope
- **NEW:** `test_apply_scope_when_filter_exists_no_microservice` — Filter without microservice gets auto-scope injected
- **NEW:** `test_apply_scope_preserves_explicit_microservice` — Explicit microservice not overridden
- **NEW:** `test_apply_scope_no_default` — No auto-detected scope leaves filter unchanged

## 7. Open questions

None — all key decisions resolved during brainstorming.

## 8. Out of scope

- Auto-detecting multiple systems and splitting indexes
- Changing index directory structure
- Global config or project registry
- Changes to indexing, query, or graph-building logic
- `init` command behavior changes (beyond the parent-config warning)
- CLI-level microservice auto-scope (MCP server only for now)
- Dynamic microscope scope re-detection if cwd changes during a long-running session (server restart required)
- Config-based microservice boundary overrides (use existing `microservice_roots` YAML field instead)

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Walk-up finds wrong config in a shared parent (e.g. `IdeaProjects/.java-codebase-rag.yml` when user meant `System-A/`) | `init` warns when a parent config exists. First-match-wins means the closest config is always preferred. If a stray config exists at a high level, it's only found when no closer config exists. |
| Symlink cycles during walk-up | `Path.resolve()` canonicalizes the path before walking. The `parent` chain on resolved paths cannot cycle. |
| Symlinked config directories skip intended config | Use `Path.resolve()` selectively — resolve for cycle detection but preserve the original logical path for config lookup. This ensures configs in symlinked dirs are still found. |
| Performance of filesystem stat calls in deep directory trees | Each step is a single `is_file()` check. Even at 20 levels deep, this is negligible compared to the embedding/indexing work the tool already does. |
| Home directory boundary varies by platform | Use `Path.home()` which returns the user home directory cross-platform (`$HOME` on Unix/macOS, `%USERPROFILE%` on Windows). The boundary is checked inclusively. |
| Nested configs create confusion (which one is active?) | First-match-wins is simple and matches git's behavior. The tool logs at INFO level which config file was discovered to aid debugging. |

## 10. Decisions taken

1. **First match wins** — closest config to cwd, not "most specific" or "deepest". Matches git behavior. No heuristic for picking among multiple configs.
2. **Home directory is inclusive boundary** — check home itself, don't go past it. Use `Path.home()` for cross-platform compatibility (works on `$HOME` for Unix/macOS, `%USERPROFILE%` on Windows). Avoids finding configs in `/` or system directories.
3. **YAML field named `source_root`** — same name as the CLI flag for conceptual consistency, despite different resolution bases. The alternative (`project_root`, `code_dir`) would add a new concept where none is needed.
4. **Walk-up is a separate pre-step** — not integrated into `resolve_operator_config()`. Cleaner separation, easier to test, lower risk to existing resolution logic.
5. **No changes to `init`** — `init` creates config + index as before. The walk-up only helps find existing configs from subdirectories.
6. **No `--walk-up` opt-out flag** — walk-up is always-on when no explicit source root is given. If a user hits the wrong config, the fix is to move or remove the stray config file, not to add a flag.
7. **Config discovery is logged** — INFO-level log message shows which config file was discovered. Optional `--debug` flag prints full discovery path and resolution chain for troubleshooting.
8. **Microservice detection reuses indexing logic** — Use the same `microservice_for_path()` function that determines microservice boundaries during indexing. Ensures consistency between indexing and querying.
9. **Microservice scope is scoped inside, all at root** — Auto-scope applies when inside a microservice directory. At source_root level, queries span all microservices with an advisory message.
10. **Server-level scope injection** — MCP server detects microservice at startup and injects into queries. Explicit filters always override auto-detected scope.

## 11. Migration plan — 1 PR

Single PR containing:
1. `discover_project_root()` function in `config.py` using `Path.home()` for cross-platform home detection
2. `source_root` YAML field parsing in `resolve_operator_config()`
3. Updated `_project_root()` in `server.py`
4. Updated `_resolved_from_ns()` in `cli.py`
5. Index dir auto-derived from discovered source root (no env var needed)
6. Soft warning in `init` when parent config detected
7. INFO-level logging of discovered config file path
8. Optional `--debug` / `--verbose` flag that prints full discovery path and resolution chain
9. All tests from §6 (including Windows-specific tests)
10. `mcp.json.example` updated to show minimal zero-env-var config
11. README update documenting the new behavior and YAML vs CLI path resolution differences with examples
12. **NEW:** `detect_microservice_from_path()` function in `graph_enrich.py`
13. **NEW:** `ScopeManager` class in `server.py`
14. **NEW:** Microservice auto-scope integration in tool wrappers
15. **NEW:** Advisory messages when queries span multiple microservices
16. **NEW:** All microservice auto-scope tests from §6
